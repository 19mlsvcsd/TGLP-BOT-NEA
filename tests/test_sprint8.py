"""
tests/test_sprint8.py
=====================
Sprint 8 verification tests for core/safety.py.

Tests are grouped into two tiers:
  1. Pure unit tests  -- no network, no keys required.
  2. Read-only on-chain tests -- lightweight RPC calls to BSC Testnet.

Run with:
  python tests/test_sprint8.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_session(chat_id=12345, paused=False, safety_locked=False):
    """Return a minimal UserSession for safety controller tests."""
    from core.strategy_manager import UserSession
    from config.settings import BALANCED_GROWTH
    return UserSession(
        chat_id=chat_id,
        wallet_address="0x0000000000000000000000000000000000000001",
        private_key="0x" + "a" * 64,
        active_strategy=BALANCED_GROWTH,
        compound_enabled=True,
        auto_execute=True,
        paused=paused,
        safety_locked=safety_locked,
    )


def _get_w3():
    from helpers.blockchain import get_web3
    return get_web3()


# ===========================================================================
# Tier 1 -- Pure unit tests (no network)
# ===========================================================================

def test_safety_check_result_defaults():
    """SafetyCheckResult must construct correctly with default reason=""."""
    from core.safety import SafetyCheckResult

    r = SafetyCheckResult(passed=True, check_name="gas_price")
    assert r.passed is True
    assert r.check_name == "gas_price"
    assert r.reason == ""

    r2 = SafetyCheckResult(passed=False, check_name="gas_reserve", reason="Not enough BNB")
    assert r2.passed is False
    assert r2.check_name == "gas_reserve"
    assert r2.reason == "Not enough BNB"

    print("[PASS] SafetyCheckResult -- defaults and construction")


def test_check_position_size_pass():
    """Position size within limit must pass."""
    from core.safety import safety_controller

    # 0.5 BNB out of 1.0 BNB = 50%, well below 90%.
    result = safety_controller.check_position_size(0.5, 1.0)
    assert result.passed is True
    assert result.check_name == "position_size"

    # Right at the limit: 90% of 1.0 BNB = 0.9 BNB.
    result2 = safety_controller.check_position_size(0.9, 1.0)
    assert result2.passed is True

    print("[PASS] check_position_size() -- amounts within limit pass")


def test_check_position_size_fail():
    """Position size exceeding limit must fail with a descriptive message."""
    from core.safety import safety_controller

    # 0.95 BNB out of 1.0 BNB = 95%, exceeds 90%.
    result = safety_controller.check_position_size(0.95, 1.0)
    assert result.passed is False
    assert "position_size" == result.check_name
    # Reason must mention the key values.
    assert "BNB" in result.reason or "%" in result.reason

    # Zero wallet balance edge case.
    result2 = safety_controller.check_position_size(0.1, 0.0)
    assert result2.passed is False

    print("[PASS] check_position_size() -- amounts over limit fail correctly")


def test_check_gas_reserve_pass():
    """Gas reserve check must pass when enough BNB remains after allocation."""
    from core.safety import safety_controller

    # After 0.9 BNB allocation from 1.0 BNB, 0.1 BNB remains (> 0.005 MIN).
    result = safety_controller.check_gas_reserve(
        wallet_balance_bnb=1.0, amount_bnb=0.9
    )
    assert result.passed is True
    assert result.check_name == "gas_reserve"

    print("[PASS] check_gas_reserve() -- sufficient reserve passes")


def test_check_gas_reserve_fail():
    """Gas reserve check must fail when the remaining balance is below MIN_BNB_FOR_GAS."""
    from core.safety import safety_controller
    from config.settings import MIN_BNB_FOR_GAS

    # After 0.005 BNB allocation from 0.006 BNB, only 0.001 BNB remains.
    result = safety_controller.check_gas_reserve(
        wallet_balance_bnb=0.006, amount_bnb=0.005
    )
    assert result.passed is False
    assert result.check_name == "gas_reserve"
    # Reason must mention remaining balance or reserve minimum.
    assert "BNB" in result.reason

    print(f"[PASS] check_gas_reserve() -- low reserve fails: {result.reason[:60]}")


def test_check_session_state_operational():
    """An active, unlocked session must pass the session state check."""
    from core.safety import safety_controller

    session = _make_session()
    result = safety_controller.check_session_state(session)
    assert result.passed is True
    assert result.check_name == "session_state"

    print("[PASS] check_session_state() -- operational session passes")


def test_check_session_state_paused():
    """A paused session must fail the session state check."""
    from core.safety import safety_controller

    session = _make_session(paused=True)
    result = safety_controller.check_session_state(session)
    assert result.passed is False
    assert "paused" in result.reason.lower()

    print("[PASS] check_session_state() -- paused session fails correctly")


def test_check_session_state_locked():
    """A safety-locked session must fail the session state check."""
    from core.safety import safety_controller

    session = _make_session(safety_locked=True)
    result = safety_controller.check_session_state(session)
    assert result.passed is False
    assert "lock" in result.reason.lower() or "safety" in result.reason.lower()

    print("[PASS] check_session_state() -- safety-locked session fails correctly")


def test_trigger_emergency_pause():
    """trigger_emergency_pause() must set safety_locked=True on the session."""
    from core.safety import safety_controller

    session = _make_session()
    assert session.safety_locked is False

    safety_controller.trigger_emergency_pause(session, "Test emergency trigger")
    assert session.safety_locked is True

    print("[PASS] trigger_emergency_pause() -- sets safety_locked on session")


def test_clear_safety_lock():
    """clear_safety_lock() must set safety_locked=False on a locked session."""
    from core.safety import safety_controller

    session = _make_session(safety_locked=True)
    assert session.safety_locked is True

    safety_controller.clear_safety_lock(session)
    assert session.safety_locked is False

    print("[PASS] clear_safety_lock() -- clears safety_locked on session")


def test_record_anomaly_escalation():
    """
    Three consecutive anomalous cycles must trigger a safety lock.
    Two cycles must not.
    """
    from core.safety import SafetyController

    sc = SafetyController()
    session = _make_session(chat_id=9001)

    # First two anomalous cycles -- below threshold (3), no lock.
    r1 = sc.record_anomaly_cycle(session, has_anomalies=True)
    assert session.safety_locked is False
    assert r1.passed is True

    r2 = sc.record_anomaly_cycle(session, has_anomalies=True)
    assert session.safety_locked is False
    assert r2.passed is True

    # Third anomalous cycle -- reaches threshold, lock must engage.
    r3 = sc.record_anomaly_cycle(session, has_anomalies=True)
    assert session.safety_locked is True
    assert r3.passed is False
    assert r3.check_name == "anomaly_escalation"
    assert len(r3.reason) > 0

    print("[PASS] record_anomaly_cycle() -- safety lock after 3 consecutive anomalies")


def test_record_anomaly_reset():
    """
    A non-anomalous cycle must reset the consecutive anomaly counter to zero.
    """
    from core.safety import SafetyController

    sc = SafetyController()
    session = _make_session(chat_id=9002)

    # Two anomalous cycles.
    sc.record_anomaly_cycle(session, has_anomalies=True)
    sc.record_anomaly_cycle(session, has_anomalies=True)
    assert sc._consecutive_anomalies.get(9002, 0) == 2

    # One clean cycle -- counter resets.
    result = sc.record_anomaly_cycle(session, has_anomalies=False)
    assert sc._consecutive_anomalies.get(9002, 0) == 0
    assert result.passed is True
    # Session should still be unlocked (never reached threshold).
    assert session.safety_locked is False

    print("[PASS] record_anomaly_cycle() -- clean cycle resets counter")


# ===========================================================================
# Tier 2 -- Read-only on-chain tests (BSC Testnet RPC, no keys needed)
# ===========================================================================

def test_check_gas_price_live():
    """
    check_gas_price() on BSC Testnet should pass -- testnet gas is ~0.1 Gwei,
    far below the 20 Gwei hard limit.
    """
    from core.safety import safety_controller

    w3 = _get_w3()
    result = safety_controller.check_gas_price(w3)
    assert result.passed is True
    assert result.check_name == "gas_price"
    assert result.reason == ""

    print("[PASS] check_gas_price() -- live testnet gas price is within limit")


def test_get_system_health():
    """
    get_system_health() must return a complete dict with correct BSC Testnet values.
    """
    from core.safety import safety_controller
    from config.settings import BSC_TESTNET_CHAIN_ID

    w3 = _get_w3()
    health = safety_controller.get_system_health(w3)

    assert health["connected"] is True
    assert health["chain_id"] == BSC_TESTNET_CHAIN_ID
    assert isinstance(health["block_number"], int) and health["block_number"] > 0
    assert isinstance(health["gas_price_gwei"], float) and health["gas_price_gwei"] >= 0
    assert isinstance(health["rpc_latency_ms"], float) and health["rpc_latency_ms"] > 0
    assert health["safe_to_trade"] is True  # testnet gas is always within limit

    print(
        f"[PASS] get_system_health() -- connected, block {health['block_number']}, "
        f"gas {health['gas_price_gwei']:.3f} Gwei, "
        f"latency {health['rpc_latency_ms']:.0f} ms"
    )


def test_run_pre_execution_checks_paused():
    """
    run_pre_execution_checks() must return a failure immediately when the
    session is paused -- no network calls needed beyond the session check.
    """
    from core.safety import safety_controller

    w3 = _get_w3()
    session = _make_session(paused=True)

    result = safety_controller.run_pre_execution_checks(w3, session, amount_bnb=0.1)
    assert result.passed is False
    assert result.check_name == "session_state"
    assert "paused" in result.reason.lower()

    print("[PASS] run_pre_execution_checks() -- paused session blocked at first check")


def test_run_pre_execution_checks_no_bnb():
    """
    run_pre_execution_checks() must fail at the gas reserve check when the
    supplied wallet balance is zero.  wallet_balance_bnb is passed directly
    so the test result does not depend on the live balance of a specific address.
    """
    from core.safety import safety_controller

    w3 = _get_w3()
    session = _make_session()

    result = safety_controller.run_pre_execution_checks(
        w3, session, amount_bnb=0.1, wallet_balance_bnb=0.0
    )
    assert result.passed is False
    # Either gas_reserve or position_size will catch this.
    assert result.check_name in ("gas_reserve", "position_size")

    print(
        f"[PASS] run_pre_execution_checks() -- zero-balance wallet blocked: "
        f"{result.check_name}: {result.reason[:60]}"
    )


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # Tier 1 -- pure unit tests
    test_safety_check_result_defaults()
    test_check_position_size_pass()
    test_check_position_size_fail()
    test_check_gas_reserve_pass()
    test_check_gas_reserve_fail()
    test_check_session_state_operational()
    test_check_session_state_paused()
    test_check_session_state_locked()
    test_trigger_emergency_pause()
    test_clear_safety_lock()
    test_record_anomaly_escalation()
    test_record_anomaly_reset()

    # Tier 2 -- read-only on-chain
    test_check_gas_price_live()
    test_get_system_health()
    test_run_pre_execution_checks_paused()
    test_run_pre_execution_checks_no_bnb()

    print()
    print("All Sprint 8 tests passed.")
