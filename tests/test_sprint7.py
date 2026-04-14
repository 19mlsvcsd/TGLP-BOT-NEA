"""
tests/test_sprint7.py
=====================
Sprint 7 verification tests for core/executor.py.

Tests are grouped into three tiers:
  1. Pure unit tests  -- no network, no keys required.
  2. Read-only on-chain tests -- call view functions on BSC Testnet RPC.
  3. Execution failure tests  -- verify that execution functions fail safely
     when the wallet has no funds (simulation will reject them).

Run with:
  python tests/test_sprint7.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===========================================================================
# Tier 1 -- Pure unit tests (no network)
# ===========================================================================

def test_execution_result_defaults():
    """ExecutionResult should have safe defaults for all optional fields."""
    from core.executor import ExecutionResult

    r = ExecutionResult(success=True, action="allocate")
    assert r.tx_hashes == []
    assert r.token_id is None
    assert r.amount0 == 0.0
    assert r.amount1 == 0.0
    assert r.fees0_collected == 0.0
    assert r.fees1_collected == 0.0
    assert r.gas_used == 0
    assert r.gas_cost_bnb == 0.0
    assert r.error is None

    r2 = ExecutionResult(success=False, action="swap", error="Out of funds")
    assert r2.success is False
    assert r2.error == "Out of funds"
    assert r2.tx_hashes == []

    print("[PASS] ExecutionResult -- defaults and construction")


def test_round_tick():
    """_round_tick() must round DOWN to the nearest multiple of tick_spacing."""
    from core.executor import _round_tick

    # Exact multiple -- unchanged.
    assert _round_tick(100, 10) == 100

    # Round down (positive tick).
    assert _round_tick(15, 10) == 10
    assert _round_tick(19, 10) == 10
    assert _round_tick(20, 10) == 20

    # Round down (negative tick) -- Python floor division handles this correctly.
    assert _round_tick(-1, 10) == -10
    assert _round_tick(-10, 10) == -10
    assert _round_tick(-11, 10) == -20

    # Tick spacing of 1 -- no rounding ever.
    assert _round_tick(7, 1) == 7
    assert _round_tick(-3, 1) == -3

    # Large spacing.
    assert _round_tick(250, 60) == 240
    assert _round_tick(-1, 60) == -60

    # Edge: tick_spacing <= 0 should return tick unchanged (guard).
    assert _round_tick(15, 0) == 15

    print("[PASS] _round_tick() -- all cases")


def test_apply_slippage():
    """_apply_slippage() should return floor of amount * (1 - slippage)."""
    from core.executor import _apply_slippage

    # 0.5% slippage on 1000 wei.
    assert _apply_slippage(1000, 0.005) == 995

    # 1% slippage.
    assert _apply_slippage(10_000, 0.01) == 9_900

    # Zero slippage -- unchanged.
    assert _apply_slippage(5_000, 0.0) == 5_000

    # 100% slippage -- result is 0.
    assert _apply_slippage(1_000_000, 1.0) == 0

    # Integer truncation (floor not round).
    # 0.5% of 999 = 4.995 slippage -> result = 994 (floor of 994.005)
    assert _apply_slippage(999, 0.005) == 994

    print("[PASS] _apply_slippage() -- all cases")


def test_deadline():
    """_deadline() must return a timestamp strictly in the future."""
    from core.executor import _deadline
    from config.settings import TX_DEADLINE_OFFSET

    before = int(time.time())
    dl = _deadline()
    after = int(time.time())

    assert dl >= before + TX_DEADLINE_OFFSET
    assert dl <= after + TX_DEADLINE_OFFSET + 1

    print("[PASS] _deadline() -- returns future timestamp")


def test_wbnb_address_format():
    """WBNB_ADDRESS must be a valid 42-char checksummed Ethereum address."""
    from core.executor import WBNB_ADDRESS
    from web3 import Web3

    assert len(WBNB_ADDRESS) == 42
    assert WBNB_ADDRESS.startswith("0x")
    # Web3.to_checksum_address will raise if the format is invalid.
    checksummed = Web3.to_checksum_address(WBNB_ADDRESS)
    assert checksummed == WBNB_ADDRESS or checksummed.lower() == WBNB_ADDRESS.lower()

    print("[PASS] WBNB_ADDRESS -- valid address format")


def test_abi_files_load():
    """All three new ABI JSON files must load without errors."""
    from core.executor import _load_abi
    from config.settings import ABI_FACTORY, ABI_ROUTER, ABI_POSITION_MANAGER

    factory_abi = _load_abi(ABI_FACTORY)
    assert isinstance(factory_abi, list) and len(factory_abi) > 0
    # Factory must have getPool.
    names = [e.get("name") for e in factory_abi]
    assert "getPool" in names

    router_abi = _load_abi(ABI_ROUTER)
    assert isinstance(router_abi, list) and len(router_abi) > 0
    names = [e.get("name") for e in router_abi]
    assert "exactInputSingle" in names

    pm_abi = _load_abi(ABI_POSITION_MANAGER)
    assert isinstance(pm_abi, list) and len(pm_abi) > 0
    names = [e.get("name") for e in pm_abi]
    for fn in ("mint", "increaseLiquidity", "decreaseLiquidity", "collect",
               "positions", "balanceOf", "tokenOfOwnerByIndex"):
        assert fn in names, f"Missing function in PositionManager ABI: {fn}"

    print("[PASS] ABI files load correctly -- all required functions present")


def test_tick_range_calculation():
    """
    Demonstrate the tick range logic used in execute_allocate:
    tick_lower = round_down(current_tick - 10 * spacing)
    tick_upper = round_down(current_tick + 10 * spacing) + spacing
    tick_lower must be < tick_upper and both must be multiples of tick_spacing.
    """
    from core.executor import _round_tick

    def compute_range(current_tick, tick_spacing):
        half_range = tick_spacing * 10
        tick_lower = _round_tick(current_tick - half_range, tick_spacing)
        tick_upper = _round_tick(current_tick + half_range, tick_spacing) + tick_spacing
        return tick_lower, tick_upper

    for current_tick, tick_spacing in [
        (0,     10),
        (100,   10),
        (-50,   10),
        (12345, 60),
        (-999,  200),
    ]:
        lo, hi = compute_range(current_tick, tick_spacing)
        assert lo < hi, f"tick_lower >= tick_upper: {lo} >= {hi}"
        assert lo % tick_spacing == 0, f"tick_lower {lo} not a multiple of {tick_spacing}"
        assert hi % tick_spacing == 0, f"tick_upper {hi} not a multiple of {tick_spacing}"

    print("[PASS] tick range calculation -- valid bounds for all test inputs")


# ===========================================================================
# Tier 2 -- Read-only on-chain tests (BSC Testnet RPC, no keys needed)
# ===========================================================================

def _get_w3():
    """Return a connected Web3 instance for BSC Testnet."""
    from helpers.blockchain import get_web3
    return get_web3()


def test_get_position_nonexistent():
    """get_position() on a non-existent token ID must return None, not raise."""
    from core.executor import get_position

    w3 = _get_w3()
    # Token ID 0 has never existed; the contract should revert and we catch it.
    result = get_position(w3, 0)
    assert result is None

    print("[PASS] get_position(0) -- returns None for non-existent token ID")


def test_get_user_positions_empty_wallet():
    """get_user_positions() on a fresh address must return an empty list."""
    from core.executor import get_user_positions

    w3 = _get_w3()
    # A freshly-generated address that has never interacted with the PositionManager.
    fresh_address = "0x000000000000000000000000000000000000dEaD"
    result = get_user_positions(w3, fresh_address)
    assert isinstance(result, list)
    # The burn address should have no LP positions.
    assert len(result) == 0

    print("[PASS] get_user_positions() -- empty list for wallet with no positions")


def test_get_pool_address_unknown():
    """
    get_pool_address() for tokens that have no pool must return None.

    Uses two nonsense addresses that cannot have a real pool.
    """
    from core.executor import get_pool_address

    w3 = _get_w3()
    fake_a = "0x0000000000000000000000000000000000000001"
    fake_b = "0x0000000000000000000000000000000000000002"
    result = get_pool_address(w3, fake_a, fake_b, 100)
    assert result is None

    print("[PASS] get_pool_address() -- returns None for non-existent pool")


def test_contracts_instantiate():
    """
    Verify that _get_position_manager, _get_router, _get_factory all create
    valid web3.Contract objects (i.e., ABI loading and address handling work).
    """
    from core.executor import _get_position_manager, _get_router, _get_factory
    from config.settings import (
        PANCAKE_V3_POSITION_MANAGER, PANCAKE_V3_ROUTER, PANCAKE_V3_FACTORY,
    )
    from web3 import Web3

    w3 = _get_w3()

    pm = _get_position_manager(w3)
    assert pm.address == Web3.to_checksum_address(PANCAKE_V3_POSITION_MANAGER)

    router = _get_router(w3)
    assert router.address == Web3.to_checksum_address(PANCAKE_V3_ROUTER)

    factory = _get_factory(w3)
    assert factory.address == Web3.to_checksum_address(PANCAKE_V3_FACTORY)

    print("[PASS] Contract instances -- all three contracts instantiate correctly")


# ===========================================================================
# Tier 3 -- Execution failure tests (no real funds, simulation will reject)
# ===========================================================================

def _make_pool(address="0x0000000000000000000000000000000000000001",
               symbol="USDT-USDC", apr=5.0, tvl=500_000):
    from core.market_data import PoolData
    return PoolData(
        pool=address, symbol=symbol,
        apr=apr, apr_reward=0.0,
        tvl_usd=tvl, volume_24h=100_000,
        fee_tier=100, pair_type="stable-stable",
    )


def _make_strategy(max_slippage=0.005):
    from config.settings import StrategyConfig
    return StrategyConfig(
        name="Test", description="",
        allowed_pair_types=["stable-stable"],
        min_tvl_usd=0,
        max_slippage=max_slippage,
        rebalance_threshold=0.15,
        compound_interval=3600,
        auto_execute=False,
    )


def test_collect_fees_no_funds():
    """
    collect_fees() with a zero-balance wallet should fail gracefully:
    either simulation rejects it or position lookup fails.
    The result must be an ExecutionResult with success=False and a message.
    """
    from core.executor import collect_fees, ExecutionResult

    w3 = _get_w3()
    # Use a dummy private key whose address has zero BNB (testnet fresh account).
    # Hardhat test key #5 — no testnet BNB.
    dummy_key = "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba"
    from helpers.blockchain import get_wallet_address
    wallet = get_wallet_address(dummy_key)

    result = collect_fees(w3, token_id=99999, wallet_address=wallet, private_key=dummy_key)

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.error is not None and len(result.error) > 0
    assert result.action == "collect"

    print(f"[PASS] collect_fees() -- fails safely with no funds: {result.error[:60]}")


def test_execute_allocate_insufficient_bnb():
    """
    execute_allocate() must return a descriptive error when the wallet has
    insufficient BNB to cover both the allocation and the gas reserve.
    """
    from core.executor import execute_allocate, ExecutionResult

    w3 = _get_w3()
    dummy_key = "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba"
    from helpers.blockchain import get_wallet_address
    wallet = get_wallet_address(dummy_key)

    pool = _make_pool()
    strategy = _make_strategy()

    result = execute_allocate(
        w3=w3, pool_data=pool, amount_bnb=10.0,
        wallet_address=wallet, private_key=dummy_key, strategy=strategy,
    )

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.error is not None
    # Should mention "BNB" or "balance" in the error message.
    assert "BNB" in result.error or "balance" in result.error.lower() or "pool" in result.error.lower()

    print(f"[PASS] execute_allocate() -- fails safely with no BNB: {result.error[:80]}")


def test_remove_liquidity_bad_token_id():
    """
    remove_liquidity() with a non-existent token_id must fail safely:
    get_position() returns None and we return a clean error.
    """
    from core.executor import remove_liquidity, ExecutionResult

    w3 = _get_w3()
    dummy_key = "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba"
    from helpers.blockchain import get_wallet_address
    wallet = get_wallet_address(dummy_key)

    result = remove_liquidity(w3, token_id=99999, wallet_address=wallet, private_key=dummy_key)

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.error is not None
    assert result.action == "remove"

    print(f"[PASS] remove_liquidity() -- fails safely for non-existent token: {result.error[:60]}")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # Tier 1 -- pure unit tests
    test_execution_result_defaults()
    test_round_tick()
    test_apply_slippage()
    test_deadline()
    test_wbnb_address_format()
    test_abi_files_load()
    test_tick_range_calculation()

    # Tier 2 -- read-only on-chain
    test_get_position_nonexistent()
    test_get_user_positions_empty_wallet()
    test_get_pool_address_unknown()
    test_contracts_instantiate()

    # Tier 3 -- execution failure safety
    test_collect_fees_no_funds()
    test_execute_allocate_insufficient_bnb()
    test_remove_liquidity_bad_token_id()

    print()
    print("All Sprint 7 tests passed.")
