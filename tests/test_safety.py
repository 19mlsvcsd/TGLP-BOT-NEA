"""
tests/test_safety.py
====================
Unit tests for core/safety.py.

Covers all SafetyController checks with injected values so no live RPC
calls are made. The live gas-price check (test_tier_2_*) is tagged with
"live" and requires BSC Testnet connectivity.

Run with:
  python tests/test_safety.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.safety import SafetyCheckResult, SafetyController
from core.strategy_manager import UserSession
from config.settings import BALANCED_GROWTH, MAX_GAS_PRICE_GWEI, MIN_BNB_FOR_GAS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(paused=False, safety_locked=False):
    s = UserSession(
        chat_id=90001,
        wallet_address="0x" + "0" * 40,
        private_key="0x" + "a" * 64,
        active_strategy=BALANCED_GROWTH,
        compound_enabled=False,
        auto_execute=False,
        paused=paused,
        safety_locked=safety_locked,
    )
    return s


def _fresh_controller():
    """Return a new SafetyController with no accumulated anomaly state."""
    return SafetyController()


# ---------------------------------------------------------------------------
# SafetyCheckResult defaults
# ---------------------------------------------------------------------------

def test_check_result_passed_default():
    """A SafetyCheckResult with passed=True must have empty reason."""
    result = SafetyCheckResult(passed=True, check_name="test")
    assert result.passed is True
    assert result.reason == ""
    print("[PASS] SafetyCheckResult — passed=True has empty reason")


def test_check_result_failed_has_reason():
    result = SafetyCheckResult(passed=False, check_name="test", reason="Too high")
    assert result.passed is False
    assert result.reason == "Too high"
    print("[PASS] SafetyCheckResult — passed=False carries reason string")


# ---------------------------------------------------------------------------
# check_session_state
# ---------------------------------------------------------------------------

def test_session_state_operational():
    ctrl = _fresh_controller()
    result = ctrl.check_session_state(_session())
    assert result.passed is True
    print("[PASS] check_session_state — operational session passes")


def test_session_state_paused():
    ctrl = _fresh_controller()
    result = ctrl.check_session_state(_session(paused=True))
    assert result.passed is False
    assert "paused" in result.reason.lower()
    print("[PASS] check_session_state — paused session fails")


def test_session_state_safety_locked():
    ctrl = _fresh_controller()
    result = ctrl.check_session_state(_session(safety_locked=True))
    assert result.passed is False
    assert "lock" in result.reason.lower() or "safe" in result.reason.lower()
    print("[PASS] check_session_state — safety-locked session fails")


# ---------------------------------------------------------------------------
# check_position_size
# ---------------------------------------------------------------------------

def test_position_size_passes():
    """Allocating 50% of wallet is within the 90% limit."""
    ctrl = _fresh_controller()
    result = ctrl.check_position_size(amount_bnb=1.0, wallet_balance_bnb=2.0)
    assert result.passed is True
    print("[PASS] check_position_size — 50% allocation passes")


def test_position_size_fails_over_limit():
    """Allocating 95% of wallet exceeds MAX_POSITION_FRACTION."""
    ctrl = _fresh_controller()
    result = ctrl.check_position_size(amount_bnb=0.95, wallet_balance_bnb=1.0)
    assert result.passed is False
    print("[PASS] check_position_size — 95% allocation blocked")


def test_position_size_zero_wallet_fails():
    """Allocation when wallet is empty must always fail."""
    ctrl = _fresh_controller()
    result = ctrl.check_position_size(amount_bnb=0.0, wallet_balance_bnb=0.0)
    assert result.passed is False
    print("[PASS] check_position_size — zero wallet balance fails")


# ---------------------------------------------------------------------------
# check_gas_reserve
# ---------------------------------------------------------------------------

def test_gas_reserve_passes():
    """After allocating, if remaining BNB covers gas, check passes."""
    ctrl = _fresh_controller()
    # wallet=1.0, allocate=0.5 → 0.5 BNB remaining, well above MIN_BNB_FOR_GAS.
    result = ctrl.check_gas_reserve(wallet_balance_bnb=1.0, amount_bnb=0.5)
    assert result.passed is True
    print(f"[PASS] check_gas_reserve — passes when remaining > {MIN_BNB_FOR_GAS} BNB")


def test_gas_reserve_fails():
    """Allocating almost the full wallet leaves insufficient gas reserve."""
    ctrl = _fresh_controller()
    # wallet=0.01, allocate=0.009 → 0.001 BNB remaining, below MIN_BNB_FOR_GAS.
    result = ctrl.check_gas_reserve(wallet_balance_bnb=0.01, amount_bnb=0.009)
    assert result.passed is False
    print("[PASS] check_gas_reserve — fails when remaining < MIN_BNB_FOR_GAS")


# ---------------------------------------------------------------------------
# trigger_emergency_pause / clear_safety_lock
# ---------------------------------------------------------------------------

def test_trigger_emergency_pause():
    ctrl = _fresh_controller()
    session = _session()
    assert session.safety_locked is False

    ctrl.trigger_emergency_pause(session, reason="Test emergency")
    assert session.safety_locked is True
    print("[PASS] trigger_emergency_pause — sets session.safety_locked=True")


def test_clear_safety_lock():
    ctrl = _fresh_controller()
    session = _session(safety_locked=True)
    ctrl.clear_safety_lock(session)
    assert session.safety_locked is False
    print("[PASS] clear_safety_lock — clears session.safety_locked")


# ---------------------------------------------------------------------------
# record_anomaly_cycle
# ---------------------------------------------------------------------------

def test_anomaly_counter_increments():
    """Consecutive anomalous cycles must increment the per-session counter."""
    ctrl = _fresh_controller()
    session = _session()

    ctrl.record_anomaly_cycle(session, has_anomalies=True)
    count = ctrl._consecutive_anomalies.get(session.chat_id, 0)
    assert count == 1
    print("[PASS] record_anomaly_cycle — counter increments on anomalous cycle")


def test_anomaly_counter_resets_on_clean_cycle():
    """A clean cycle must reset the consecutive counter to 0."""
    ctrl = _fresh_controller()
    session = _session()

    ctrl.record_anomaly_cycle(session, has_anomalies=True)
    ctrl.record_anomaly_cycle(session, has_anomalies=True)
    ctrl.record_anomaly_cycle(session, has_anomalies=False)

    count = ctrl._consecutive_anomalies.get(session.chat_id, 0)
    assert count == 0
    print("[PASS] record_anomaly_cycle — counter resets to 0 on clean cycle")


def test_anomaly_counter_triggers_safety_lock():
    """After SAFETY_ANOMALY_LOCK_THRESHOLD consecutive anomalies, safety lock engages."""
    from config.settings import SAFETY_ANOMALY_LOCK_THRESHOLD
    ctrl = _fresh_controller()
    session = _session()

    # Fire exactly threshold-1 anomalous cycles — no lock yet.
    for _ in range(SAFETY_ANOMALY_LOCK_THRESHOLD - 1):
        result = ctrl.record_anomaly_cycle(session, has_anomalies=True)
        assert result.passed is True
        assert session.safety_locked is False

    # The threshold-th cycle must trigger the lock.
    result = ctrl.record_anomaly_cycle(session, has_anomalies=True)
    assert result.passed is False
    assert session.safety_locked is True
    print(f"[PASS] record_anomaly_cycle — lock after {SAFETY_ANOMALY_LOCK_THRESHOLD} consecutive anomalies")


def test_reset_anomaly_counter():
    """reset_anomaly_counter must clear the per-session counter."""
    ctrl = _fresh_controller()
    session = _session()

    ctrl.record_anomaly_cycle(session, has_anomalies=True)
    ctrl.record_anomaly_cycle(session, has_anomalies=True)
    ctrl.reset_anomaly_counter(session.chat_id)

    count = ctrl._consecutive_anomalies.get(session.chat_id, 0)
    assert count == 0
    print("[PASS] reset_anomaly_counter — counter cleared to 0")


# ---------------------------------------------------------------------------
# run_pre_execution_checks
# ---------------------------------------------------------------------------

def test_pre_checks_paused_session_blocked():
    """A paused session must be blocked at the session_state check."""
    ctrl = _fresh_controller()
    session = _session(paused=True)

    from unittest.mock import MagicMock
    mock_w3 = MagicMock()

    result = ctrl.run_pre_execution_checks(
        mock_w3, session, amount_bnb=0.5, wallet_balance_bnb=1.0
    )
    assert result.passed is False
    assert result.check_name == "session_state"
    print("[PASS] run_pre_execution_checks — paused session blocked at session_state")


def test_pre_checks_zero_balance_blocked():
    """A zero wallet balance must be blocked at the gas_reserve check."""
    ctrl = _fresh_controller()
    session = _session()

    from unittest.mock import MagicMock
    mock_w3 = MagicMock()
    # Inject a near-zero gas price so gas_price check passes.
    mock_w3.eth.gas_price = 1_000_000_000  # 1 Gwei (below MAX_GAS_PRICE_GWEI)

    result = ctrl.run_pre_execution_checks(
        mock_w3, session, amount_bnb=0.0, wallet_balance_bnb=0.0
    )
    assert result.passed is False
    print(f"[PASS] run_pre_execution_checks — zero balance blocked at {result.check_name}")


def test_pre_checks_all_pass_with_good_inputs():
    """Sane inputs with a mocked gas price must produce passed=True."""
    ctrl = _fresh_controller()
    session = _session()

    from unittest.mock import MagicMock
    mock_w3 = MagicMock()
    # 1 Gwei — well below MAX_GAS_PRICE_GWEI.
    mock_w3.eth.gas_price = 1_000_000_000

    # wallet=2.0 BNB, allocate=0.5 BNB → 1.5 BNB remaining (well above gas reserve).
    result = ctrl.run_pre_execution_checks(
        mock_w3, session, amount_bnb=0.5, wallet_balance_bnb=2.0
    )
    assert result.passed is True
    print("[PASS] run_pre_execution_checks — all checks pass with sane inputs")


# ===========================================================================
# Tier 2 — live BSC Testnet (gas price only)
# ===========================================================================

def test_check_gas_price_live():
    """Live gas price on BSC Testnet must be well below MAX_GAS_PRICE_GWEI=20."""
    from helpers.blockchain import get_web3
    ctrl = _fresh_controller()
    w3 = get_web3()
    result = ctrl.check_gas_price(w3)
    assert result.passed is True, (
        f"Gas price check failed: {result.reason}. "
        f"(Is BSC Testnet gas price above {MAX_GAS_PRICE_GWEI} Gwei?)"
    )
    print(f"[PASS] check_gas_price — live testnet gas price below {MAX_GAS_PRICE_GWEI} Gwei limit")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # SafetyCheckResult
    test_check_result_passed_default()
    test_check_result_failed_has_reason()

    # check_session_state
    test_session_state_operational()
    test_session_state_paused()
    test_session_state_safety_locked()

    # check_position_size
    test_position_size_passes()
    test_position_size_fails_over_limit()
    test_position_size_zero_wallet_fails()

    # check_gas_reserve
    test_gas_reserve_passes()
    test_gas_reserve_fails()

    # emergency pause / clear lock
    test_trigger_emergency_pause()
    test_clear_safety_lock()

    # anomaly counter
    test_anomaly_counter_increments()
    test_anomaly_counter_resets_on_clean_cycle()
    test_anomaly_counter_triggers_safety_lock()
    test_reset_anomaly_counter()

    # run_pre_execution_checks
    test_pre_checks_paused_session_blocked()
    test_pre_checks_zero_balance_blocked()
    test_pre_checks_all_pass_with_good_inputs()

    # Tier 2 — live
    test_check_gas_price_live()

    print()
    print("All safety tests passed.")
