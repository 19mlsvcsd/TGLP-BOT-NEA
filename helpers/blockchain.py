"""
helpers/blockchain.py
=====================
Low-level blockchain interface for TGLP Bot.

This module owns the Web3 connection and every interaction with the BSC
Testnet RPC: reading balances, estimating gas, building transactions,
simulating them via eth_call, signing, broadcasting, and waiting for receipts.

All other modules that need on-chain data call functions from here. No other
module should instantiate Web3 directly; this keeps the RPC URL, chain ID,
and connection logic in one place so they are easy to update or swap out.

Security note: private keys are passed in as arguments only, they are never
stored, logged, or written anywhere by this module.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import TxParams, TxReceipt

from config.settings import (
    ABI_ERC20,
    BSC_TESTNET_CHAIN_ID,
    BSC_TESTNET_RPC_URL,
    GAS_LIMIT_APPROVE,
    TX_DEADLINE_OFFSET,
    TX_RECEIPT_TIMEOUT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ABI loader
# ---------------------------------------------------------------------------

def _load_abi(path: str) -> list:
    """
    Load a JSON ABI file from disk and return it as a Python list.

    The path is resolved relative to the project root (the directory that
    contains config/). We resolve at call time rather than at import time so
    that the working directory does not need to be set before importing this
    module.

    Args:
        path: Relative path to the ABI JSON file, e.g. 'config/abi/erc20.json'.

    Returns:
        The ABI as a list of dicts.

    Raises:
        FileNotFoundError: If the ABI file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    # Walk up from this file's directory to find the project root.
    # helpers/blockchain.py → helpers/ → project root
    project_root = Path(__file__).parent.parent
    full_path = project_root / path
    with open(full_path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_web3(rpc_url: Optional[str] = None) -> Web3:
    """
    Create and return a Web3 instance connected to BSC Testnet.

    Verifies the connection is live and that the connected chain matches
    BSC_TESTNET_CHAIN_ID (97). Raises an exception if either check fails;
    the caller should catch this during startup and abort rather than operate
    on the wrong network.

    Args:
        rpc_url: Override the RPC endpoint. Defaults to the environment
                 variable BSC_TESTNET_RPC_URL, then the settings constant.

    Returns:
        A connected Web3 instance.

    Raises:
        ConnectionError: If the RPC is unreachable.
        ValueError: If the connected chain ID is not BSC Testnet (97).
    """
    url = rpc_url or os.getenv("BSC_TESTNET_RPC_URL", BSC_TESTNET_RPC_URL)
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))

    if not w3.is_connected():
        raise ConnectionError(
            f"Cannot connect to BSC Testnet RPC at {url}. "
            "Check your internet connection or try a different endpoint."
        )

    chain_id = w3.eth.chain_id
    if chain_id != BSC_TESTNET_CHAIN_ID:
        raise ValueError(
            f"Connected to chain ID {chain_id}, expected {BSC_TESTNET_CHAIN_ID} "
            "(BSC Testnet). Check the RPC URL in your .env file."
        )

    logger.info("Connected to BSC Testnet (chain ID %d) via %s", chain_id, url)
    return w3


# ---------------------------------------------------------------------------
# Wallet utilities
# ---------------------------------------------------------------------------

def get_wallet_address(private_key: str) -> str:
    """
    Derive the public wallet address from a private key.

    Args:
        private_key: 64-character hex private key (with or without '0x' prefix).
                     Must already be validated by helpers/validators.py.

    Returns:
        The checksummed Ethereum address string, e.g. '0xAb5801a...'.
    """
    # eth_account.Account expects the key with the 0x prefix.
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    account = Account.from_key(private_key)
    return account.address


def get_bnb_balance(w3: Web3, address: str) -> float:
    """
    Return the native BNB balance of an address in human-readable form.

    BNB balances are stored as integer Wei on-chain. This function converts
    to Ether units (18 decimals) and returns a float.

    Args:
        w3:      Connected Web3 instance.
        address: Checksummed wallet or contract address.

    Returns:
        BNB balance as a float, e.g. 0.5123.

    Raises:
        Exception: Propagates any Web3 RPC error.
    """
    wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    return float(w3.from_wei(wei, "ether"))


# ---------------------------------------------------------------------------
# Token utilities
# ---------------------------------------------------------------------------

def get_token_balance(w3: Web3, token_address: str, wallet_address: str) -> float:
    """
    Read the ERC-20 token balance of a wallet and return it in human-readable
    form (i.e., divided by 10 ** decimals).

    Args:
        w3:             Connected Web3 instance.
        token_address:  Checksummed address of the ERC-20 token contract.
        wallet_address: Checksummed wallet address to query.

    Returns:
        Token balance as a float adjusted for the token's decimal places.
        Returns 0.0 if the call fails (e.g., not a valid ERC-20).
    """
    try:
        abi = _load_abi(ABI_ERC20)
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=abi
        )
        raw = token.functions.balanceOf(
            Web3.to_checksum_address(wallet_address)
        ).call()
        decimals = token.functions.decimals().call()
        return raw / (10 ** decimals)
    except Exception as e:
        logger.warning(
            "Failed to read token balance for %s: %s", token_address, e
        )
        return 0.0


def get_token_info(w3: Web3, token_address: str) -> Dict[str, Any]:
    """
    Read basic metadata from an ERC-20 token contract.

    Args:
        w3:            Connected Web3 instance.
        token_address: Checksummed token contract address.

    Returns:
        Dict with keys: 'name' (str), 'symbol' (str), 'decimals' (int).
        Values default to empty strings / 18 decimals on any RPC error.
    """
    result: Dict[str, Any] = {"name": "", "symbol": "", "decimals": 18}
    try:
        abi = _load_abi(ABI_ERC20)
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=abi
        )
        result["name"] = token.functions.name().call()
        result["symbol"] = token.functions.symbol().call()
        result["decimals"] = token.functions.decimals().call()
    except Exception as e:
        logger.warning("Failed to read token info for %s: %s", token_address, e)
    return result


# ---------------------------------------------------------------------------
# Gas utilities
# ---------------------------------------------------------------------------

def get_gas_price_gwei(w3: Web3) -> float:
    """
    Return the current network gas price in Gwei.

    Used by the safety controller to check whether gas prices are within
    acceptable limits before executing a transaction.

    Args:
        w3: Connected Web3 instance.

    Returns:
        Gas price in Gwei as a float.
    """
    gas_price_wei = w3.eth.gas_price
    return float(w3.from_wei(gas_price_wei, "gwei"))


def estimate_gas(w3: Web3, tx: TxParams) -> Tuple[int, float]:
    """
    Estimate the gas units and BNB cost for a transaction.

    Uses eth_estimateGas with a 20% buffer on top of the raw estimate to
    reduce the chance of running out of gas mid-execution. The BNB cost
    is calculated using the current network gas price.

    Args:
        w3: Connected Web3 instance.
        tx: Transaction dict (must include 'to', 'from', and optionally 'data').

    Returns:
        Tuple of (gas_limit: int, cost_in_bnb: float).
        gas_limit includes the 20% safety buffer.
        Returns (300_000, 0.0) on estimation failure.
    """
    try:
        raw_estimate = w3.eth.estimate_gas(tx)
        # Apply a 20% buffer: integer division would lose precision, so use ceil.
        buffered = int(raw_estimate * 1.2)
        gas_price_wei = w3.eth.gas_price
        cost_wei = buffered * gas_price_wei
        cost_bnb = float(w3.from_wei(cost_wei, "ether"))
        return buffered, cost_bnb
    except Exception as e:
        logger.warning("Gas estimation failed: %s", e)
        return 300_000, 0.0


# ---------------------------------------------------------------------------
# Transaction building
# ---------------------------------------------------------------------------

def build_tx(
    w3: Web3,
    from_address: str,
    to_address: str,
    data: bytes = b"",
    value: int = 0,
    gas_limit: Optional[int] = None,
) -> TxParams:
    """
    Construct a complete, ready-to-sign transaction dictionary.

    Automatically fetches the current nonce and gas price from the network.
    The chain ID is set to BSC_TESTNET_CHAIN_ID to prevent replay attacks.

    Args:
        w3:           Connected Web3 instance.
        from_address: Checksummed sender address.
        to_address:   Checksummed recipient/contract address.
        data:         Encoded function call data (bytes). Empty for plain BNB sends.
        value:        BNB value in Wei to attach to the transaction.
        gas_limit:    Override the gas limit. If None, estimate_gas() is called.

    Returns:
        A TxParams dict ready to pass to sign_and_send().
    """
    nonce = w3.eth.get_transaction_count(
        Web3.to_checksum_address(from_address), "pending"
    )
    gas_price = w3.eth.gas_price

    tx: TxParams = {
        "from": Web3.to_checksum_address(from_address),
        "to": Web3.to_checksum_address(to_address),
        "nonce": nonce,
        "gasPrice": gas_price,
        "chainId": BSC_TESTNET_CHAIN_ID,
        "value": value,
        "data": data,
    }

    if gas_limit is not None:
        tx["gas"] = gas_limit
    else:
        estimated, _ = estimate_gas(w3, tx)
        tx["gas"] = estimated

    return tx


# ---------------------------------------------------------------------------
# Transaction simulation
# ---------------------------------------------------------------------------

def simulate_transaction(w3: Web3, tx: TxParams) -> Tuple[bool, str]:
    """
    Dry-run a transaction via eth_call to check if it would revert.

    This is a critical safety step: we simulate every transaction before
    broadcasting it. A simulation costs no gas and catches most revert
    conditions (insufficient balance, wrong parameters, contract errors)
    before spending real BNB on a failing transaction.

    Args:
        w3: Connected Web3 instance.
        tx: Fully built transaction dict from build_tx().

    Returns:
        Tuple of (success: bool, message: str).
        success=True means the call did not revert.
        success=False includes the revert reason in message.
    """
    try:
        w3.eth.call(tx)
        return True, "Simulation passed."
    except ContractLogicError as e:
        # ContractLogicError carries the revert reason from the contract.
        reason = str(e)
        logger.warning("Transaction simulation reverted: %s", reason)
        return False, f"Transaction would revert: {reason}"
    except Exception as e:
        # Other exceptions (RPC error, timeout): report but do not crash.
        logger.warning("Transaction simulation failed with unexpected error: %s", e)
        return False, f"Simulation error: {e}"


# ---------------------------------------------------------------------------
# Transaction signing and broadcast
# ---------------------------------------------------------------------------

def sign_and_send(
    w3: Web3, tx: TxParams, private_key: str
) -> Optional[TxReceipt]:
    """
    Sign a transaction, broadcast it, and wait for the receipt.

    The function blocks until the transaction is mined or TX_RECEIPT_TIMEOUT
    seconds have elapsed. On timeout, None is returned so the caller can
    decide whether to retry or report an error.

    Args:
        w3:          Connected Web3 instance.
        tx:          Fully built transaction dict from build_tx().
        private_key: Raw private key string (with or without '0x').
                     NEVER logged, NEVER stored.

    Returns:
        The transaction receipt dict on success.
        None if the transaction timed out waiting to be mined.

    Raises:
        Exception: Any signing or broadcast error is propagated to the caller.
    """
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

    logger.info("Transaction broadcast: %s", tx_hash.hex())

    # Poll for the receipt. web3.py's wait_for_transaction_receipt raises
    # TimeExhausted if the timeout is exceeded.
    try:
        receipt = w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=TX_RECEIPT_TIMEOUT
        )
        status = "confirmed" if receipt.status == 1 else "reverted"
        logger.info(
            "Transaction %s: status %s, gas used: %d",
            tx_hash.hex(), status, receipt.gasUsed,
        )
        return receipt
    except Exception as e:
        logger.warning(
            "Timed out waiting for receipt for %s: %s", tx_hash.hex(), e
        )
        return None


# ---------------------------------------------------------------------------
# ERC-20 approval
# ---------------------------------------------------------------------------

def approve_token(
    w3: Web3,
    token_address: str,
    spender: str,
    amount: int,
    private_key: str,
) -> Optional[TxReceipt]:
    """
    Approve a spender contract to transfer up to `amount` of an ERC-20 token.

    Before submitting an approval, the current allowance is checked. If the
    existing allowance already covers `amount`, the approval transaction is
    skipped entirely to save gas.

    This is called by executor.py before every swap or liquidity operation
    that moves ERC-20 tokens into a router or position manager contract.

    Args:
        w3:            Connected Web3 instance.
        token_address: Checksummed address of the token to approve.
        spender:       Checksummed address of the contract being approved
                       (e.g., PancakeSwap V3 router).
        amount:        Amount to approve, in raw token units (Wei-equivalent).
        private_key:   Private key for signing. NEVER logged or stored.

    Returns:
        The tx receipt if an approval was sent, or None if allowance was
        already sufficient (no transaction needed) or on failure.
    """
    wallet = get_wallet_address(private_key)

    try:
        abi = _load_abi(ABI_ERC20)
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=abi
        )

        # Check current allowance before submitting a new approval.
        current_allowance = token.functions.allowance(
            Web3.to_checksum_address(wallet),
            Web3.to_checksum_address(spender),
        ).call()

        if current_allowance >= amount:
            logger.info(
                "Allowance already sufficient for %s (current: %d, needed: %d)",
                token_address, current_allowance, amount,
            )
            return None  # No transaction needed.

        # Build the approval call data.
        approve_data = token.encodeABI(
            fn_name="approve",
            args=[Web3.to_checksum_address(spender), amount],
        )

        tx = build_tx(
            w3,
            from_address=wallet,
            to_address=token_address,
            data=approve_data,
            gas_limit=GAS_LIMIT_APPROVE,
        )

        # Simulate before sending.
        ok, message = simulate_transaction(w3, tx)
        if not ok:
            logger.error("Approval simulation failed: %s", message)
            return None

        receipt = sign_and_send(w3, tx, private_key)
        if receipt and receipt.status == 1:
            logger.info(
                "Approval confirmed for %s → spender %s", token_address, spender
            )
        else:
            logger.error(
                "Approval transaction failed or timed out for %s", token_address
            )
        return receipt

    except Exception as e:
        logger.error("approve_token failed for %s: %s", token_address, e)
        return None


# ---------------------------------------------------------------------------
# RPC health check
# ---------------------------------------------------------------------------

def get_rpc_latency_ms(w3: Web3) -> float:
    """
    Measure the round-trip latency of a lightweight RPC call in milliseconds.

    Used by the safety controller's get_system_health() to report whether
    the RPC endpoint is responding quickly. A single eth_blockNumber call is
    used as it is the cheapest possible RPC request.

    Args:
        w3: Connected Web3 instance.

    Returns:
        Latency in milliseconds, or -1.0 if the call fails.
    """
    try:
        start = time.monotonic()
        w3.eth.block_number
        end = time.monotonic()
        return (end - start) * 1000
    except Exception as e:
        logger.warning("RPC latency check failed: %s", e)
        return -1.0
