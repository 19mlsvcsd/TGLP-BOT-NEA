"""
core/executor.py
================
On-chain execution layer for TGLP Bot.

This module translates DecisionResults into signed blockchain transactions.
It is the ONLY module that broadcasts transactions to the network; the
decision engine (Sprint 6) and dispatcher (Sprint 10) are purely analytical.

Execution contract:
    Every write function in this module must:
    1. Check preconditions (balance, position existence).
    2. Call simulate_transaction() before sign_and_send(), no exceptions.
    3. Return a structured ExecutionResult so the dispatcher can log and
       report the outcome regardless of success or failure.

The three high-level entry points the dispatcher calls are:
    execute_allocate():  enter a new LP position
    execute_rebalance(): exit current position, enter new pool
    execute_compound():  collect fees and reinvest into same position

Design: all state is passed in as arguments. This module holds no mutable
state. It communicates with the blockchain via helpers/blockchain.py and
reads pool contracts via the ABIs in config/abi/.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from web3 import Web3

from config.settings import (
    ABI_ERC20,
    ABI_FACTORY,
    ABI_POOL,
    ABI_POSITION_MANAGER,
    ABI_ROUTER,
    GAS_LIMIT_ADD_LIQUIDITY,
    GAS_LIMIT_COLLECT,
    GAS_LIMIT_REMOVE_LIQUIDITY,
    GAS_LIMIT_SWAP,
    MIN_BNB_FOR_GAS,
    PANCAKE_V3_FACTORY,
    PANCAKE_V3_POSITION_MANAGER,
    PANCAKE_V3_ROUTER,
    TX_DEADLINE_OFFSET,
)
from core.market_data import PoolData
from helpers.blockchain import (
    approve_token,
    build_tx,
    get_bnb_balance,
    simulate_transaction,
    sign_and_send,
)

logger = logging.getLogger(__name__)

# WBNB (Wrapped BNB) address on BSC Testnet.
# Used to identify native-BNB pools so the executor can attach value to the tx.
WBNB_ADDRESS: str = "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"

# Maximum uint128 value, used as amount0Max/amount1Max in collect() to
# withdraw all available fees in one call.
_MAX_UINT128: int = 2 ** 128 - 1


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    """
    Outcome of any on-chain execution attempt.

    Returned by every execute_* function. A result with success=False carries
    a human-readable error message but no transaction hashes.

    Attributes:
        success:         True if all on-chain steps completed without error.
        action:          The operation performed: 'allocate', 'rebalance',
                         'compound', 'collect', 'remove', 'swap', 'add_liquidity'.
        tx_hashes:       Ordered list of transaction hashes broadcast in this action.
        token_id:        NFT token ID of a newly minted LP position (ALLOCATE only).
        amount0:         token0 amount involved in the action (human-readable float).
        amount1:         token1 amount involved in the action.
        fees0_collected: token0 fees collected (COMPOUND/COLLECT only).
        fees1_collected: token1 fees collected.
        gas_used:        Total gas units consumed across all transactions.
        gas_cost_bnb:    Total gas cost in BNB at the network price at execution time.
        error:           Human-readable error description if success=False.
    """
    success: bool
    action: str
    tx_hashes: List[str] = field(default_factory=list)
    token_id: Optional[int] = None
    amount0: float = 0.0
    amount1: float = 0.0
    fees0_collected: float = 0.0
    fees1_collected: float = 0.0
    gas_used: int = 0
    gas_cost_bnb: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# ABI / contract helpers
# ---------------------------------------------------------------------------

def _load_abi(path: str) -> list:
    """Load a JSON ABI from disk, resolving the path relative to project root."""
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(__file__).parent.parent / path
    with open(resolved, "r") as f:
        return json.load(f)


def _get_position_manager(w3: Web3):
    """Return a web3.Contract instance for the NonfungiblePositionManager."""
    return w3.eth.contract(
        address=Web3.to_checksum_address(PANCAKE_V3_POSITION_MANAGER),
        abi=_load_abi(ABI_POSITION_MANAGER),
    )


def _get_router(w3: Web3):
    """Return a web3.Contract instance for the PancakeSwap V3 Router."""
    return w3.eth.contract(
        address=Web3.to_checksum_address(PANCAKE_V3_ROUTER),
        abi=_load_abi(ABI_ROUTER),
    )


def _get_factory(w3: Web3):
    """Return a web3.Contract instance for the PancakeSwap V3 Factory."""
    return w3.eth.contract(
        address=Web3.to_checksum_address(PANCAKE_V3_FACTORY),
        abi=_load_abi(ABI_FACTORY),
    )


# ---------------------------------------------------------------------------
# Pure utility helpers
# ---------------------------------------------------------------------------

def _round_tick(tick: int, tick_spacing: int) -> int:
    """
    Round a tick value DOWN to the nearest multiple of tick_spacing.

    PancakeSwap V3 requires tickLower and tickUpper to be exact multiples
    of the pool's tickSpacing or the mint() call will revert.

    Args:
        tick:         The raw tick value to round.
        tick_spacing: The pool's tick spacing (e.g. 1, 10, 60, 200).

    Returns:
        The largest multiple of tick_spacing that is ≤ tick.
    """
    if tick_spacing <= 0:
        return tick
    # Python's floor division handles negative ticks correctly.
    return (tick // tick_spacing) * tick_spacing


def _deadline() -> int:
    """Return a transaction deadline TX_DEADLINE_OFFSET seconds from now."""
    return int(time.time()) + TX_DEADLINE_OFFSET


def _apply_slippage(amount_wei: int, slippage: float) -> int:
    """
    Calculate the minimum acceptable output after slippage.

    Args:
        amount_wei: The desired token amount in smallest unit (wei).
        slippage:   Slippage tolerance as a fraction (e.g. 0.005 = 0.5%).

    Returns:
        Floor of amount_wei * (1 - slippage) as an integer.
    """
    return int(amount_wei * (1.0 - slippage))


def _get_token_balance_raw(w3: Web3, token_address: str, wallet_address: str) -> int:
    """
    Return the raw ERC-20 balance of wallet_address in the token's smallest unit.

    This is the balanceOf() return value before dividing by 10**decimals.
    Used when passing amounts to contract calls that expect raw wei-like values.

    Returns 0 on any RPC failure.
    """
    try:
        abi = _load_abi(ABI_ERC20)
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=abi
        )
        return token.functions.balanceOf(
            Web3.to_checksum_address(wallet_address)
        ).call()
    except Exception as exc:
        logger.warning("_get_token_balance_raw(%s) failed: %s", token_address[:10], exc)
        return 0


# ---------------------------------------------------------------------------
# Pool data helper
# ---------------------------------------------------------------------------

def _get_pool_tokens_and_tick(
    w3: Web3, pool_address: str
) -> Optional[Dict[str, Any]]:
    """
    Read token0, token1, fee, tickSpacing, and currentTick from a pool contract.

    Uses the full pool ABI (config/abi/pancake_pool_v3.json) which exposes
    token0(), token1(), tickSpacing(), slot0(), and fee().

    Returns:
        Dict with keys: token0, token1, fee, tick_spacing, current_tick.
        Returns None if any RPC call fails.
    """
    try:
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=_load_abi(ABI_POOL),
        )
        token0 = pool.functions.token0().call()
        token1 = pool.functions.token1().call()
        fee = pool.functions.fee().call()
        tick_spacing = pool.functions.tickSpacing().call()
        slot0 = pool.functions.slot0().call()
        current_tick = slot0[1]
        return {
            "token0": Web3.to_checksum_address(token0),
            "token1": Web3.to_checksum_address(token1),
            "fee": fee,
            "tick_spacing": tick_spacing,
            "current_tick": current_tick,
        }
    except Exception as exc:
        logger.warning("_get_pool_tokens_and_tick(%s) failed: %s", pool_address[:10], exc)
        return None


# ---------------------------------------------------------------------------
# Read functions (no gas, no signing)
# ---------------------------------------------------------------------------

def get_position(w3: Web3, token_id: int) -> Optional[Dict[str, Any]]:
    """
    Read position data from the NonfungiblePositionManager.

    Args:
        w3:       Connected Web3 instance.
        token_id: NFT token ID of the position.

    Returns:
        Dict with keys: nonce, operator, token0, token1, fee, tickLower,
        tickUpper, liquidity, feeGrowthInside0LastX128,
        feeGrowthInside1LastX128, tokensOwed0, tokensOwed1.
        Returns None if the call fails (e.g., non-existent token ID).
    """
    try:
        pm = _get_position_manager(w3)
        result = pm.functions.positions(token_id).call()
        keys = [
            "nonce", "operator", "token0", "token1", "fee",
            "tickLower", "tickUpper", "liquidity",
            "feeGrowthInside0LastX128", "feeGrowthInside1LastX128",
            "tokensOwed0", "tokensOwed1",
        ]
        return dict(zip(keys, result))
    except Exception as exc:
        logger.warning("get_position(%d) failed: %s", token_id, exc)
        return None


def get_user_positions(w3: Web3, wallet_address: str) -> List[int]:
    """
    Return a list of LP position token IDs owned by wallet_address.

    Uses the ERC-721 enumeration interface on the NonfungiblePositionManager.

    Args:
        w3:             Connected Web3 instance.
        wallet_address: Address to enumerate.

    Returns:
        List of integer token IDs. Empty list if the wallet has no positions
        or the call fails.
    """
    try:
        pm = _get_position_manager(w3)
        addr = Web3.to_checksum_address(wallet_address)
        count = pm.functions.balanceOf(addr).call()
        token_ids = []
        for i in range(count):
            tid = pm.functions.tokenOfOwnerByIndex(addr, i).call()
            token_ids.append(tid)
        return token_ids
    except Exception as exc:
        logger.warning("get_user_positions(%s) failed: %s", wallet_address[:10], exc)
        return []


def get_pool_address(
    w3: Web3, token0: str, token1: str, fee: int
) -> Optional[str]:
    """
    Look up a pool address from the V3 Factory.

    Args:
        w3:     Connected Web3 instance.
        token0: First token address.
        token1: Second token address.
        fee:    Fee tier in bps (e.g. 100, 500, 2500, 10000).

    Returns:
        Checksummed pool address string, or None if the pool does not exist
        or the call fails.
    """
    try:
        factory = _get_factory(w3)
        addr = factory.functions.getPool(
            Web3.to_checksum_address(token0),
            Web3.to_checksum_address(token1),
            fee,
        ).call()
        if addr == "0x0000000000000000000000000000000000000000":
            return None
        return addr
    except Exception as exc:
        logger.warning("get_pool_address() failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# collect_fees
# ---------------------------------------------------------------------------

def collect_fees(
    w3: Web3,
    token_id: int,
    wallet_address: str,
    private_key: str,
) -> ExecutionResult:
    """
    Collect all accrued fees from a V3 LP position.

    Calls PositionManager.collect() with _MAX_UINT128 for both amounts to
    withdraw everything available in one transaction.

    Args:
        w3:             Connected Web3 instance.
        token_id:       NFT token ID of the position.
        wallet_address: Position owner's wallet address.
        private_key:    Owner's private key for signing.

    Returns:
        ExecutionResult with success flag, tx_hash, and gas details.
    """
    try:
        pm = _get_position_manager(w3)
        gas_price_wei = w3.eth.gas_price

        collect_params = {
            "tokenId": token_id,
            "recipient": Web3.to_checksum_address(wallet_address),
            "amount0Max": _MAX_UINT128,
            "amount1Max": _MAX_UINT128,
        }
        data = pm.encode_abi("collect", args=[collect_params])

        tx = build_tx(
            w3=w3,
            from_address=wallet_address,
            to_address=PANCAKE_V3_POSITION_MANAGER,
            data=data,
            value=0,
            gas_limit=GAS_LIMIT_COLLECT,
        )

        ok, sim_err = simulate_transaction(w3, tx)
        if not ok:
            return ExecutionResult(
                success=False, action="collect",
                error=f"Simulation failed: {sim_err}",
            )

        receipt = sign_and_send(w3, tx, private_key)
        if receipt is None:
            return ExecutionResult(
                success=False, action="collect",
                error="Transaction was not mined within the timeout period.",
            )

        gas_used = receipt["gasUsed"]
        return ExecutionResult(
            success=True,
            action="collect",
            tx_hashes=[receipt["transactionHash"].hex()],
            gas_used=gas_used,
            gas_cost_bnb=(gas_used * gas_price_wei) / 1e18,
        )

    except Exception as exc:
        logger.error("collect_fees() unhandled error: %s", exc, exc_info=True)
        return ExecutionResult(success=False, action="collect", error=str(exc))


# ---------------------------------------------------------------------------
# remove_liquidity
# ---------------------------------------------------------------------------

def remove_liquidity(
    w3: Web3,
    token_id: int,
    wallet_address: str,
    private_key: str,
    slippage: float = 0.005,
) -> ExecutionResult:
    """
    Remove all liquidity from a V3 position and collect the resulting tokens.

    Two-step sequence:
    1. decreaseLiquidity(liquidity=full_amount, amount0Min=0, amount1Min=0)
       converts LP shares back to token0 + token1, held in the position.
    2. collect(amount0Max=MAX, amount1Max=MAX)
       withdraws those tokens (plus any pre-existing uncollected fees)
       to the wallet.

    If liquidity is already 0 (position was previously drained), step 1 is
    skipped and only collect() is called.

    Args:
        w3:             Connected Web3 instance.
        token_id:       NFT token ID of the position.
        wallet_address: Owner's address.
        private_key:    Owner's private key.
        slippage:       Not applied to minima here (set to 0 for safety on
                        full withdrawals; partial withdrawals can add slippage).

    Returns:
        ExecutionResult combining gas from both transactions.
    """
    action = "remove"
    tx_hashes: List[str] = []
    total_gas = 0
    total_cost = 0.0

    try:
        pm = _get_position_manager(w3)
        gas_price_wei = w3.eth.gas_price

        # ── Read current liquidity ────────────────────────────────────────
        position = get_position(w3, token_id)
        if position is None:
            return ExecutionResult(
                success=False, action=action,
                error=f"Cannot read position {token_id}; may not exist.",
            )

        liquidity = position["liquidity"]

        # ── Step 1: decreaseLiquidity (skip if already empty) ─────────────
        if liquidity > 0:
            decrease_params = {
                "tokenId": token_id,
                "liquidity": liquidity,
                "amount0Min": 0,
                "amount1Min": 0,
                "deadline": _deadline(),
            }
            data = pm.encode_abi("decreaseLiquidity", args=[decrease_params])
            tx = build_tx(
                w3=w3,
                from_address=wallet_address,
                to_address=PANCAKE_V3_POSITION_MANAGER,
                data=data,
                value=0,
                gas_limit=GAS_LIMIT_REMOVE_LIQUIDITY,
            )

            ok, sim_err = simulate_transaction(w3, tx)
            if not ok:
                return ExecutionResult(
                    success=False, action=action,
                    error=f"decreaseLiquidity simulation failed: {sim_err}",
                )

            receipt = sign_and_send(w3, tx, private_key)
            if receipt is None:
                return ExecutionResult(
                    success=False, action=action,
                    error="decreaseLiquidity transaction was not mined.",
                )
            total_gas += receipt["gasUsed"]
            total_cost += (receipt["gasUsed"] * gas_price_wei) / 1e18
            tx_hashes.append(receipt["transactionHash"].hex())

        # ── Step 2: collect everything ────────────────────────────────────
        collect_params = {
            "tokenId": token_id,
            "recipient": Web3.to_checksum_address(wallet_address),
            "amount0Max": _MAX_UINT128,
            "amount1Max": _MAX_UINT128,
        }
        data = pm.encode_abi("collect", args=[collect_params])
        tx = build_tx(
            w3=w3,
            from_address=wallet_address,
            to_address=PANCAKE_V3_POSITION_MANAGER,
            data=data,
            value=0,
            gas_limit=GAS_LIMIT_COLLECT,
        )

        ok, sim_err = simulate_transaction(w3, tx)
        if not ok:
            return ExecutionResult(
                success=False, action=action,
                tx_hashes=tx_hashes,
                gas_used=total_gas,
                gas_cost_bnb=total_cost,
                error=f"collect simulation failed: {sim_err}",
            )

        receipt = sign_and_send(w3, tx, private_key)
        if receipt is None:
            return ExecutionResult(
                success=False, action=action,
                tx_hashes=tx_hashes,
                gas_used=total_gas,
                gas_cost_bnb=total_cost,
                error="collect transaction was not mined.",
            )
        total_gas += receipt["gasUsed"]
        total_cost += (receipt["gasUsed"] * gas_price_wei) / 1e18
        tx_hashes.append(receipt["transactionHash"].hex())

        return ExecutionResult(
            success=True,
            action=action,
            tx_hashes=tx_hashes,
            gas_used=total_gas,
            gas_cost_bnb=total_cost,
        )

    except Exception as exc:
        logger.error("remove_liquidity() unhandled error: %s", exc, exc_info=True)
        return ExecutionResult(
            success=False, action=action,
            tx_hashes=tx_hashes,
            gas_used=total_gas,
            gas_cost_bnb=total_cost,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# swap_exact_input_single
# ---------------------------------------------------------------------------

def swap_exact_input_single(
    w3: Web3,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in_wei: int,
    recipient: str,
    private_key: str,
    slippage: float = 0.005,
    bnb_value: int = 0,
) -> ExecutionResult:
    """
    Execute a single-hop V3 swap: exact amount in, minimum amount out.

    For native BNB → ERC-20 swaps, pass:
      token_in   = WBNB_ADDRESS
      bnb_value  = amount_in_wei  (native BNB attached to the transaction)

    The Router wraps the BNB to WBNB internally, no pre-wrapping needed.

    amountOutMinimum is set to 0 (accept any output) because we are on a
    testnet with shallow liquidity. On mainnet, a price oracle would be used
    to calculate a realistic minimum.

    Args:
        w3:            Connected Web3 instance.
        token_in:      Address of the input token (WBNB_ADDRESS for native BNB).
        token_out:     Address of the output token.
        fee:           Pool fee tier in bps.
        amount_in_wei: Exact input amount in the token's smallest unit.
        recipient:     Wallet that will receive the output tokens.
        private_key:   Signing key.
        slippage:      Accepted for interface consistency, not applied here
                       (amountOutMinimum = 0 in testnet mode).
        bnb_value:     Native BNB in wei to attach (only for BNB → token swaps).

    Returns:
        ExecutionResult with success flag and gas details.
    """
    try:
        router = _get_router(w3)
        gas_price_wei = w3.eth.gas_price

        swap_params = {
            "tokenIn": Web3.to_checksum_address(token_in),
            "tokenOut": Web3.to_checksum_address(token_out),
            "fee": fee,
            "recipient": Web3.to_checksum_address(recipient),
            "deadline": _deadline(),
            "amountIn": amount_in_wei,
            "amountOutMinimum": 0,   # testnet: accept any output
            "sqrtPriceLimitX96": 0,  # no price limit
        }
        data = router.encode_abi("exactInputSingle", args=[swap_params])

        tx = build_tx(
            w3=w3,
            from_address=recipient,
            to_address=PANCAKE_V3_ROUTER,
            data=data,
            value=bnb_value,
            gas_limit=GAS_LIMIT_SWAP,
        )

        ok, sim_err = simulate_transaction(w3, tx)
        if not ok:
            return ExecutionResult(
                success=False, action="swap",
                error=f"Swap simulation failed: {sim_err}",
            )

        receipt = sign_and_send(w3, tx, private_key)
        if receipt is None:
            return ExecutionResult(
                success=False, action="swap",
                error="Swap transaction was not mined within the timeout period.",
            )

        gas_used = receipt["gasUsed"]
        return ExecutionResult(
            success=True,
            action="swap",
            tx_hashes=[receipt["transactionHash"].hex()],
            gas_used=gas_used,
            gas_cost_bnb=(gas_used * gas_price_wei) / 1e18,
        )

    except Exception as exc:
        logger.error("swap_exact_input_single() unhandled error: %s", exc, exc_info=True)
        return ExecutionResult(success=False, action="swap", error=str(exc))


# ---------------------------------------------------------------------------
# add_liquidity  (mint new position)
# ---------------------------------------------------------------------------

def add_liquidity(
    w3: Web3,
    token0: str,
    token1: str,
    fee_tier: int,
    amount0_desired_wei: int,
    amount1_desired_wei: int,
    tick_lower: int,
    tick_upper: int,
    wallet_address: str,
    private_key: str,
    slippage: float = 0.005,
    bnb_value: int = 0,
) -> ExecutionResult:
    """
    Mint a new V3 LP position via NonfungiblePositionManager.mint().

    Caller is responsible for:
    - Calling approve_token() for each non-native token before this function.
    - Ensuring token0 < token1 (address order, as required by V3).
    - Computing a valid tick range (both must be multiples of tickSpacing).

    Any token amounts not used by the PositionManager (due to ratio mismatch)
    remain in the wallet; the contract never takes more than it needs.

    Args:
        w3:                   Connected Web3 instance.
        token0:               Checksummed address of the lower token (address order).
        token1:               Checksummed address of the upper token.
        fee_tier:             Pool fee tier in bps.
        amount0_desired_wei:  Maximum token0 to use (raw units).
        amount1_desired_wei:  Maximum token1 to use (raw units).
        tick_lower:           Lower tick bound (multiple of tickSpacing).
        tick_upper:           Upper tick bound (must be > tick_lower).
        wallet_address:       Owner address (receives the NFT).
        private_key:          Signing key.
        slippage:             Sets amount0Min / amount1Min.
        bnb_value:            Native BNB to attach (for WBNB-paired pools).

    Returns:
        ExecutionResult with success flag and gas details.
    """
    try:
        pm = _get_position_manager(w3)
        gas_price_wei = w3.eth.gas_price

        mint_params = {
            "token0": Web3.to_checksum_address(token0),
            "token1": Web3.to_checksum_address(token1),
            "fee": fee_tier,
            "tickLower": tick_lower,
            "tickUpper": tick_upper,
            "amount0Desired": amount0_desired_wei,
            "amount1Desired": amount1_desired_wei,
            "amount0Min": _apply_slippage(amount0_desired_wei, slippage),
            "amount1Min": _apply_slippage(amount1_desired_wei, slippage),
            "recipient": Web3.to_checksum_address(wallet_address),
            "deadline": _deadline(),
        }
        data = pm.encode_abi("mint", args=[mint_params])

        tx = build_tx(
            w3=w3,
            from_address=wallet_address,
            to_address=PANCAKE_V3_POSITION_MANAGER,
            data=data,
            value=bnb_value,
            gas_limit=GAS_LIMIT_ADD_LIQUIDITY,
        )

        ok, sim_err = simulate_transaction(w3, tx)
        if not ok:
            return ExecutionResult(
                success=False, action="add_liquidity",
                error=f"Mint simulation failed: {sim_err}",
            )

        receipt = sign_and_send(w3, tx, private_key)
        if receipt is None:
            return ExecutionResult(
                success=False, action="add_liquidity",
                error="Mint transaction was not mined within the timeout period.",
            )

        gas_used = receipt["gasUsed"]
        return ExecutionResult(
            success=True,
            action="add_liquidity",
            tx_hashes=[receipt["transactionHash"].hex()],
            gas_used=gas_used,
            gas_cost_bnb=(gas_used * gas_price_wei) / 1e18,
        )

    except Exception as exc:
        logger.error("add_liquidity() unhandled error: %s", exc, exc_info=True)
        return ExecutionResult(
            success=False, action="add_liquidity", error=str(exc)
        )


# ---------------------------------------------------------------------------
# High-level: execute_allocate
# ---------------------------------------------------------------------------

def execute_allocate(
    w3: Web3,
    pool_data: PoolData,
    amount_bnb: float,
    wallet_address: str,
    private_key: str,
    strategy: Any,
) -> ExecutionResult:
    """
    Execute an ALLOCATE action: enter a new LP position using BNB capital.

    Execution plan (pool contains WBNB):
      1. Balance check.
      2. Read on-chain pool data (token0/token1/fee/tickSpacing/currentTick).
      3. Calculate tick range: ±10 × tickSpacing around current tick.
      4. Swap half the allocation (BNB) to the non-WBNB token.
      5. Approve the non-WBNB token for the PositionManager.
      6. Mint LP position (pass other half as native BNB value).

    Execution plan (pool contains no WBNB):
      1–3 as above.
      4. Swap all BNB to token0.
      5. Swap half of token0 to token1.
      6. Approve both tokens for the PositionManager.
      7. Mint LP position.

    Args:
        w3:            Connected Web3 instance.
        pool_data:     Target pool (address used for on-chain lookup).
        amount_bnb:    Total BNB capital to allocate.
        wallet_address: Owner's wallet.
        private_key:   Owner's private key.
        strategy:      Active StrategyConfig (provides max_slippage).

    Returns:
        ExecutionResult with full transaction history and gas totals.
    """
    action = "allocate"
    tx_hashes: List[str] = []
    total_gas = 0
    total_cost = 0.0

    try:
        # ── Pre-flight balance check ──────────────────────────────────────
        bnb_balance = get_bnb_balance(w3, wallet_address)
        needed = amount_bnb + MIN_BNB_FOR_GAS
        if bnb_balance < needed:
            return ExecutionResult(
                success=False, action=action,
                error=(
                    f"Insufficient BNB: have {bnb_balance:.4f}, "
                    f"need {needed:.4f} "
                    f"({amount_bnb:.4f} to allocate + {MIN_BNB_FOR_GAS} gas reserve)."
                ),
            )

        # ── Read on-chain pool state ──────────────────────────────────────
        pool_info = _get_pool_tokens_and_tick(w3, pool_data.pool)
        if pool_info is None:
            return ExecutionResult(
                success=False, action=action,
                error=f"Could not read on-chain state for pool {pool_data.pool}.",
            )

        token0 = pool_info["token0"]
        token1 = pool_info["token1"]
        fee_tier = pool_info["fee"]
        tick_spacing = pool_info["tick_spacing"]
        current_tick = pool_info["current_tick"]

        # V3 requires token0 < token1 in address order.
        if int(token0, 16) > int(token1, 16):
            token0, token1 = token1, token0

        # ── Tick range: ±10 tick spacings around current tick ─────────────
        half_range = tick_spacing * 10
        tick_lower = _round_tick(current_tick - half_range, tick_spacing)
        tick_upper = _round_tick(current_tick + half_range, tick_spacing) + tick_spacing

        slippage = strategy.max_slippage
        wbnb = Web3.to_checksum_address(WBNB_ADDRESS)
        half_wei = w3.to_wei(amount_bnb / 2, "ether")

        wbnb_is_token0 = token0.lower() == wbnb.lower()
        wbnb_is_token1 = token1.lower() == wbnb.lower()

        # ── Case A: one token is WBNB ─────────────────────────────────────
        if wbnb_is_token0 or wbnb_is_token1:
            other_token = token1 if wbnb_is_token0 else token0

            # Swap half BNB → other token.
            swap_r = swap_exact_input_single(
                w3=w3, token_in=wbnb, token_out=other_token,
                fee=fee_tier, amount_in_wei=half_wei, recipient=wallet_address,
                private_key=private_key, slippage=slippage, bnb_value=half_wei,
            )
            if not swap_r.success:
                return ExecutionResult(
                    success=False, action=action,
                    error=f"Swap BNB to {other_token[:8]} failed: {swap_r.error}",
                )
            tx_hashes.extend(swap_r.tx_hashes)
            total_gas += swap_r.gas_used
            total_cost += swap_r.gas_cost_bnb

            # Approve other token for PositionManager.
            other_bal = _get_token_balance_raw(w3, other_token, wallet_address)
            if other_bal > 0:
                apr_receipt = approve_token(
                    w3, other_token, PANCAKE_V3_POSITION_MANAGER, other_bal, private_key
                )
                if apr_receipt:
                    tx_hashes.append(apr_receipt["transactionHash"].hex())
                    total_gas += apr_receipt["gasUsed"]

            amount0_desired = half_wei if wbnb_is_token0 else other_bal
            amount1_desired = other_bal if wbnb_is_token0 else half_wei
            bnb_val = half_wei

        # ── Case B: neither token is WBNB ─────────────────────────────────
        else:
            total_wei = w3.to_wei(amount_bnb, "ether")

            # Swap all BNB → token0.
            swap0 = swap_exact_input_single(
                w3=w3, token_in=wbnb, token_out=token0,
                fee=fee_tier, amount_in_wei=total_wei, recipient=wallet_address,
                private_key=private_key, slippage=slippage, bnb_value=total_wei,
            )
            if not swap0.success:
                return ExecutionResult(
                    success=False, action=action,
                    error=f"Swap BNB to token0 failed: {swap0.error}",
                )
            tx_hashes.extend(swap0.tx_hashes)
            total_gas += swap0.gas_used
            total_cost += swap0.gas_cost_bnb

            t0_bal = _get_token_balance_raw(w3, token0, wallet_address)
            half_t0 = t0_bal // 2

            # Approve token0 for the Router (to swap half to token1).
            apr0_r = approve_token(w3, token0, PANCAKE_V3_ROUTER, t0_bal, private_key)
            if apr0_r:
                tx_hashes.append(apr0_r["transactionHash"].hex())
                total_gas += apr0_r["gasUsed"]

            # Swap half token0 → token1.
            swap1 = swap_exact_input_single(
                w3=w3, token_in=token0, token_out=token1,
                fee=fee_tier, amount_in_wei=half_t0, recipient=wallet_address,
                private_key=private_key, slippage=slippage, bnb_value=0,
            )
            if not swap1.success:
                return ExecutionResult(
                    success=False, action=action,
                    error=f"Swap token0 to token1 failed: {swap1.error}",
                )
            tx_hashes.extend(swap1.tx_hashes)
            total_gas += swap1.gas_used
            total_cost += swap1.gas_cost_bnb

            t1_bal = _get_token_balance_raw(w3, token1, wallet_address)

            # Approve both tokens for PositionManager.
            for tok, amt in [(token0, half_t0), (token1, t1_bal)]:
                if amt > 0:
                    r = approve_token(w3, tok, PANCAKE_V3_POSITION_MANAGER, amt, private_key)
                    if r:
                        tx_hashes.append(r["transactionHash"].hex())
                        total_gas += r["gasUsed"]

            amount0_desired = half_t0
            amount1_desired = t1_bal
            bnb_val = 0

        # ── Mint LP position ──────────────────────────────────────────────
        mint_r = add_liquidity(
            w3=w3,
            token0=token0, token1=token1, fee_tier=fee_tier,
            amount0_desired_wei=amount0_desired,
            amount1_desired_wei=amount1_desired,
            tick_lower=tick_lower, tick_upper=tick_upper,
            wallet_address=wallet_address, private_key=private_key,
            slippage=slippage, bnb_value=bnb_val,
        )
        tx_hashes.extend(mint_r.tx_hashes)
        total_gas += mint_r.gas_used
        total_cost += mint_r.gas_cost_bnb

        if not mint_r.success:
            return ExecutionResult(
                success=False, action=action,
                tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
                error=f"Mint failed: {mint_r.error}",
            )

        return ExecutionResult(
            success=True, action=action,
            tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
        )

    except Exception as exc:
        logger.error("execute_allocate() unhandled error: %s", exc, exc_info=True)
        return ExecutionResult(
            success=False, action=action,
            tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# High-level: execute_rebalance
# ---------------------------------------------------------------------------

def execute_rebalance(
    w3: Web3,
    token_id: int,
    new_pool_data: PoolData,
    amount_bnb: float,
    wallet_address: str,
    private_key: str,
    strategy: Any,
) -> ExecutionResult:
    """
    Execute a REBALANCE: exit the current LP position then enter a new pool.

    Steps:
    1. remove_liquidity(): close the existing position (decreaseLiquidity + collect).
    2. execute_allocate(): enter the new pool with the freed capital.

    The amount_bnb parameter is a caller-supplied estimate of the capital
    freed from the exit. The dispatcher calculates this from the current
    position value before calling rebalance. The executor trusts this value
    for the allocation step.

    Args:
        w3:            Connected Web3 instance.
        token_id:      NFT token ID of the current position.
        new_pool_data: Target pool from the decision engine.
        amount_bnb:    Estimated BNB value freed after removing liquidity.
        wallet_address: Owner's wallet.
        private_key:   Owner's private key.
        strategy:      Active StrategyConfig.

    Returns:
        Combined ExecutionResult from both the exit and entry steps.
    """
    action = "rebalance"

    # Step 1: exit current position.
    remove_r = remove_liquidity(w3, token_id, wallet_address, private_key, strategy.max_slippage)
    if not remove_r.success:
        return ExecutionResult(
            success=False, action=action,
            tx_hashes=remove_r.tx_hashes,
            gas_used=remove_r.gas_used,
            gas_cost_bnb=remove_r.gas_cost_bnb,
            error=f"Exit step failed: {remove_r.error}",
        )

    # Step 2: enter new pool.
    alloc_r = execute_allocate(w3, new_pool_data, amount_bnb, wallet_address, private_key, strategy)

    combined_hashes = remove_r.tx_hashes + alloc_r.tx_hashes
    combined_gas = remove_r.gas_used + alloc_r.gas_used
    combined_cost = remove_r.gas_cost_bnb + alloc_r.gas_cost_bnb

    if not alloc_r.success:
        return ExecutionResult(
            success=False, action=action,
            tx_hashes=combined_hashes,
            gas_used=combined_gas,
            gas_cost_bnb=combined_cost,
            error=f"Entry step failed: {alloc_r.error}",
        )

    return ExecutionResult(
        success=True, action=action,
        tx_hashes=combined_hashes,
        gas_used=combined_gas,
        gas_cost_bnb=combined_cost,
    )


# ---------------------------------------------------------------------------
# High-level: execute_compound
# ---------------------------------------------------------------------------

def execute_compound(
    w3: Web3,
    token_id: int,
    pool_data: PoolData,
    wallet_address: str,
    private_key: str,
    strategy: Any,
) -> ExecutionResult:
    """
    Execute a COMPOUND: collect accrued fees and reinvest into the same position.

    Steps:
    1. collect_fees(): withdraw token0 + token1 fees to the wallet.
    2. Read current position data (tick range).
    3. Approve collected tokens for the PositionManager.
    4. increaseLiquidity(): add the fee tokens back into the existing position.

    This keeps the same NFT token ID and tick range, avoiding the gas cost of
    burning + reminting.

    Args:
        w3:            Connected Web3 instance.
        token_id:      NFT token ID of the position.
        pool_data:     Pool data (used for logging; not needed for on-chain calls).
        wallet_address: Owner's wallet.
        private_key:   Owner's private key.
        strategy:      Active StrategyConfig (provides max_slippage).

    Returns:
        ExecutionResult covering both the collect and increaseLiquidity steps.
    """
    action = "compound"
    tx_hashes: List[str] = []
    total_gas = 0
    total_cost = 0.0

    try:
        # ── Step 1: collect earned fees ───────────────────────────────────
        collect_r = collect_fees(w3, token_id, wallet_address, private_key)
        if not collect_r.success:
            return ExecutionResult(
                success=False, action=action,
                error=f"Fee collection failed: {collect_r.error}",
            )
        tx_hashes.extend(collect_r.tx_hashes)
        total_gas += collect_r.gas_used
        total_cost += collect_r.gas_cost_bnb

        # ── Step 2: read position for tick range ──────────────────────────
        position = get_position(w3, token_id)
        if position is None:
            return ExecutionResult(
                success=False, action=action,
                tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
                error=f"Could not read position {token_id} for reinvestment.",
            )

        token0 = position["token0"]
        token1 = position["token1"]

        t0_bal = _get_token_balance_raw(w3, token0, wallet_address)
        t1_bal = _get_token_balance_raw(w3, token1, wallet_address)

        if t0_bal == 0 and t1_bal == 0:
            return ExecutionResult(
                success=True, action=action,
                tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
                error="Fees collected but zero balance; nothing to reinvest.",
            )

        # ── Step 3: approve tokens for PositionManager ────────────────────
        gas_price_wei = w3.eth.gas_price
        for tok, bal in [(token0, t0_bal), (token1, t1_bal)]:
            if bal > 0:
                r = approve_token(w3, tok, PANCAKE_V3_POSITION_MANAGER, bal, private_key)
                if r:
                    tx_hashes.append(r["transactionHash"].hex())
                    total_gas += r["gasUsed"]
                    total_cost += (r["gasUsed"] * gas_price_wei) / 1e18

        # ── Step 4: increaseLiquidity ─────────────────────────────────────
        pm = _get_position_manager(w3)
        increase_params = {
            "tokenId": token_id,
            "amount0Desired": t0_bal,
            "amount1Desired": t1_bal,
            "amount0Min": 0,
            "amount1Min": 0,
            "deadline": _deadline(),
        }
        data = pm.encode_abi("increaseLiquidity", args=[increase_params])
        tx = build_tx(
            w3=w3,
            from_address=wallet_address,
            to_address=PANCAKE_V3_POSITION_MANAGER,
            data=data,
            value=0,
            gas_limit=GAS_LIMIT_ADD_LIQUIDITY,
        )

        ok, sim_err = simulate_transaction(w3, tx)
        if not ok:
            return ExecutionResult(
                success=False, action=action,
                tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
                error=f"increaseLiquidity simulation failed: {sim_err}",
            )

        receipt = sign_and_send(w3, tx, private_key)
        if receipt is None:
            return ExecutionResult(
                success=False, action=action,
                tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
                error="increaseLiquidity transaction was not mined.",
            )
        total_gas += receipt["gasUsed"]
        total_cost += (receipt["gasUsed"] * gas_price_wei) / 1e18
        tx_hashes.append(receipt["transactionHash"].hex())

        return ExecutionResult(
            success=True, action=action,
            tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
        )

    except Exception as exc:
        logger.error("execute_compound() unhandled error: %s", exc, exc_info=True)
        return ExecutionResult(
            success=False, action=action,
            tx_hashes=tx_hashes, gas_used=total_gas, gas_cost_bnb=total_cost,
            error=str(exc),
        )
