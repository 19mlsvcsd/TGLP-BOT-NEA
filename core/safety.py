"""
core/safety.py
==============
Safety controller for TGLP Bot.

This module acts as the last line of defence before any on-chain execution.
Every write path in executor.py calls run_pre_execution_checks() first, and
the dispatcher calls record_anomaly_cycle() after each analysis cycle to
detect sustained bad data.

Checks performed before execution:
  1. Session state -- is the session paused or already safety-locked?
  2. Gas price -- is the network gas price below the hard ceiling?
  3. Gas reserve -- will the wallet retain MIN_BNB_FOR_GAS after the trade?
  4. Position size -- is the allocation within MAX_POSITION_FRACTION of the wallet?

Anomaly escalation:
  The dispatcher calls record_anomaly_cycle() each cycle. If
  SAFETY_ANOMALY_LOCK_THRESHOLD consecutive cycles report anomalies, the
  session is safety-locked until the user manually clears it via /settings.

Design: SafetyController is a plain class instantiated once as the module-level
`safety_controller` singleton. All state (consecutive anomaly counters) lives
in the instance. The dispatcher and executor import the singleton directly.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from web3 import Web3

from config.settings import (
    MAX_GAS_PRICE_GWEI,
    MAX_POSITION_FRACTION,
    MIN_BNB_FOR_GAS,
    SAFETY_ANOMALY_LOCK_THRESHOLD,
)
from helpers.blockchain import get_bnb_balance, get_gas_price_gwei, get_rpc_latency_ms

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SafetyCheckResult:
    """
    The outcome of a single safety check.

    Attributes:
        passed:     True if the check succeeded; False if execution should be
                    blocked.
        check_name: Short identifier for the check that produced this result.
                    Used for logging and test assertions.
        reason:     Human-readable explanation, populated only when passed=False
                    (or when a non-blocking warning is attached).
    """
    passed: bool
    check_name: str
    reason: str = ""


# ---------------------------------------------------------------------------
# SafetyController
# ---------------------------------------------------------------------------

class SafetyController:
    """
    Stateful safety guard for all execution decisions.

    Holds per-user consecutive anomaly counters so that it can escalate a
    safety lock after repeated bad-data cycles. All other methods are
    stateless and operate only on their arguments.
    """

    def __init__(self) -> None:
        # Maps chat_id (int) -> number of consecutive anomalous cycles seen.
        self._consecutive_anomalies: Dict[int, int] = {}

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_gas_price(
        self,
        w3: Web3,
        max_gwei: Optional[float] = None,
    ) -> SafetyCheckResult:
        """
        Check that the current network gas price is below the hard ceiling.

        Args:
            w3:       Connected Web3 instance.
            max_gwei: Override for MAX_GAS_PRICE_GWEI (useful in tests).

        Returns:
            SafetyCheckResult -- passed if gas is acceptable.
        """
        limit = max_gwei if max_gwei is not None else MAX_GAS_PRICE_GWEI
        try:
            current = get_gas_price_gwei(w3)
        except Exception as exc:
            msg = f"Could not read gas price: {exc}"
            logger.error(msg)
            return SafetyCheckResult(passed=False, check_name="gas_price", reason=msg)

        if current > limit:
            reason = (
                f"Gas price {current:.2f} Gwei exceeds hard limit "
                f"{limit:.2f} Gwei -- execution blocked."
            )
            logger.warning(reason)
            return SafetyCheckResult(passed=False, check_name="gas_price", reason=reason)

        logger.debug("Gas price check passed: %.2f Gwei (limit %.2f Gwei)", current, limit)
        return SafetyCheckResult(passed=True, check_name="gas_price")

    def check_position_size(
        self,
        amount_bnb: float,
        wallet_balance_bnb: float,
        max_fraction: Optional[float] = None,
    ) -> SafetyCheckResult:
        """
        Check that the allocation does not exceed MAX_POSITION_FRACTION of the
        wallet balance.

        Prevents the bot from concentrating the entire wallet into one trade.

        Args:
            amount_bnb:         BNB amount to allocate.
            wallet_balance_bnb: Current wallet BNB balance.
            max_fraction:       Override for MAX_POSITION_FRACTION (useful in tests).

        Returns:
            SafetyCheckResult -- passed if the position size is acceptable.
        """
        limit = max_fraction if max_fraction is not None else MAX_POSITION_FRACTION

        if wallet_balance_bnb <= 0:
            reason = (
                f"Wallet balance is {wallet_balance_bnb:.6f} BNB -- "
                "cannot allocate from an empty wallet."
            )
            return SafetyCheckResult(
                passed=False, check_name="position_size", reason=reason
            )

        fraction = amount_bnb / wallet_balance_bnb
        if fraction > limit:
            reason = (
                f"Allocation of {amount_bnb:.4f} BNB is "
                f"{fraction * 100:.1f}% of wallet balance "
                f"({wallet_balance_bnb:.4f} BNB), exceeding the "
                f"{limit * 100:.0f}% position size limit."
            )
            logger.warning(reason)
            return SafetyCheckResult(
                passed=False, check_name="position_size", reason=reason
            )

        return SafetyCheckResult(passed=True, check_name="position_size")

    def check_gas_reserve(
        self,
        wallet_balance_bnb: float,
        amount_bnb: float,
    ) -> SafetyCheckResult:
        """
        Check that after the allocation the wallet will retain at least
        MIN_BNB_FOR_GAS BNB to cover future gas costs.

        Args:
            wallet_balance_bnb: Current wallet BNB balance.
            amount_bnb:         BNB amount about to be allocated.

        Returns:
            SafetyCheckResult -- passed if sufficient reserve will remain.
        """
        remaining = wallet_balance_bnb - amount_bnb
        if remaining < MIN_BNB_FOR_GAS:
            reason = (
                f"After allocating {amount_bnb:.4f} BNB, only "
                f"{remaining:.6f} BNB would remain, below the "
                f"{MIN_BNB_FOR_GAS:.4f} BNB gas reserve minimum."
            )
            logger.warning(reason)
            return SafetyCheckResult(
                passed=False, check_name="gas_reserve", reason=reason
            )

        return SafetyCheckResult(passed=True, check_name="gas_reserve")

    def check_session_state(self, session: Any) -> SafetyCheckResult:
        """
        Check that the session is neither paused by the user nor locked by
        the safety controller.

        Args:
            session: A UserSession instance from core/strategy_manager.py.

        Returns:
            SafetyCheckResult -- passed if the session is fully operational.
        """
        if session.safety_locked:
            reason = (
                "Session is safety-locked due to repeated anomalies or a "
                "critical error. Use /settings to clear the safety lock."
            )
            return SafetyCheckResult(
                passed=False, check_name="session_state", reason=reason
            )

        if session.paused:
            reason = (
                "Session is paused by the user. "
                "Use /settings to resume automated cycling."
            )
            return SafetyCheckResult(
                passed=False, check_name="session_state", reason=reason
            )

        return SafetyCheckResult(passed=True, check_name="session_state")

    # ------------------------------------------------------------------
    # Composite pre-execution check
    # ------------------------------------------------------------------

    def run_pre_execution_checks(
        self,
        w3: Web3,
        session: Any,
        amount_bnb: float,
        wallet_balance_bnb: Optional[float] = None,
    ) -> SafetyCheckResult:
        """
        Run all safety checks in sequence before any on-chain execution.

        Checks are ordered from cheapest (no network) to most expensive
        (network calls) so that failures are caught as early as possible.
        Returns immediately on the first failing check.

        Args:
            w3:                  Connected Web3 instance.
            session:             UserSession for the executing user.
            amount_bnb:          BNB amount to be allocated.
            wallet_balance_bnb:  Pre-read wallet balance. If None, the balance
                                 is fetched live from the RPC. Accepted as an
                                 override to simplify testing.

        Returns:
            The first SafetyCheckResult with passed=False, or a passed result
            if all checks succeed.
        """
        # 1. Session state (no network needed).
        result = self.check_session_state(session)
        if not result.passed:
            return result

        # 2. Gas price (one RPC call).
        result = self.check_gas_price(w3)
        if not result.passed:
            return result

        # 3. Read wallet balance if not provided.
        if wallet_balance_bnb is None:
            try:
                wallet_balance_bnb = get_bnb_balance(w3, session.wallet_address)
            except Exception as exc:
                msg = f"Could not read wallet balance: {exc}"
                logger.error(msg)
                return SafetyCheckResult(
                    passed=False, check_name="wallet_balance", reason=msg
                )

        # 4. Gas reserve check.
        result = self.check_gas_reserve(wallet_balance_bnb, amount_bnb)
        if not result.passed:
            return result

        # 5. Position size check.
        result = self.check_position_size(amount_bnb, wallet_balance_bnb)
        if not result.passed:
            return result

        logger.debug(
            "All pre-execution checks passed for chat_id %d "
            "(amount %.4f BNB, balance %.4f BNB)",
            session.chat_id, amount_bnb, wallet_balance_bnb,
        )
        return SafetyCheckResult(passed=True, check_name="all")

    # ------------------------------------------------------------------
    # Emergency pause / safety lock
    # ------------------------------------------------------------------

    def trigger_emergency_pause(self, session: Any, reason: str) -> None:
        """
        Engage the safety lock on a session, blocking all future execution
        until the user manually clears it.

        Args:
            session: UserSession to lock.
            reason:  Human-readable explanation logged for audit purposes.
        """
        session.safety_locked = True
        logger.error(
            "SAFETY LOCK engaged for chat_id %d: %s",
            session.chat_id, reason,
        )

    def clear_safety_lock(self, session: Any) -> None:
        """
        Clear the safety lock on a session, allowing execution to resume.

        This is called by the /settings handler when the user explicitly
        acknowledges the issue and chooses to unlock.

        Args:
            session: UserSession to unlock.
        """
        session.safety_locked = False
        logger.info(
            "Safety lock cleared for chat_id %d by user request.",
            session.chat_id,
        )

    # ------------------------------------------------------------------
    # Anomaly escalation
    # ------------------------------------------------------------------

    def record_anomaly_cycle(
        self,
        session: Any,
        has_anomalies: bool,
    ) -> SafetyCheckResult:
        """
        Record whether the latest analysis cycle detected anomalies and
        escalate to a safety lock if the threshold is exceeded.

        A non-anomalous cycle resets the counter for that user. This ensures
        the lock is only triggered by a sustained run of bad data, not a
        single outlier.

        Args:
            session:       UserSession being tracked.
            has_anomalies: True if the cycle's AnalysisResult contained any
                           anomalous pools; False otherwise.

        Returns:
            SafetyCheckResult -- passed=False and safety lock engaged if the
            threshold was reached; passed=True otherwise.
        """
        chat_id = session.chat_id

        if not has_anomalies:
            self._consecutive_anomalies[chat_id] = 0
            return SafetyCheckResult(passed=True, check_name="anomaly_escalation")

        count = self._consecutive_anomalies.get(chat_id, 0) + 1
        self._consecutive_anomalies[chat_id] = count

        logger.warning(
            "Anomalous cycle %d/%d for chat_id %d",
            count, SAFETY_ANOMALY_LOCK_THRESHOLD, chat_id,
        )

        if count >= SAFETY_ANOMALY_LOCK_THRESHOLD:
            reason = (
                f"{count} consecutive anomalous cycles detected. "
                "Safety lock engaged -- please check market conditions "
                "and use /settings to unlock when ready."
            )
            self.trigger_emergency_pause(session, reason)
            return SafetyCheckResult(
                passed=False, check_name="anomaly_escalation", reason=reason
            )

        return SafetyCheckResult(
            passed=True,
            check_name="anomaly_escalation",
            reason=(
                f"Anomaly cycle {count}/{SAFETY_ANOMALY_LOCK_THRESHOLD} "
                "-- monitoring continued."
            ),
        )

    def reset_anomaly_counter(self, chat_id: int) -> None:
        """
        Manually reset the consecutive anomaly counter for a user.

        Called by the dispatcher when the user clears the safety lock, so
        that a fresh start begins with a clean count.

        Args:
            chat_id: Telegram chat ID.
        """
        self._consecutive_anomalies[chat_id] = 0
        logger.info("Anomaly counter reset for chat_id %d", chat_id)

    # ------------------------------------------------------------------
    # System health
    # ------------------------------------------------------------------

    def get_system_health(self, w3: Web3) -> Dict:
        """
        Read live system metrics from the RPC and return a health summary.

        Used by the dispatcher to populate the /status command response and
        to gate the first cycle of a freshly started bot.

        Args:
            w3: Connected Web3 instance.

        Returns:
            Dict with keys:
              connected (bool)        -- True if the RPC responded.
              chain_id (int|None)     -- Network chain ID (expected: 97).
              block_number (int|None) -- Latest mined block number.
              gas_price_gwei (float|None) -- Current gas price.
              rpc_latency_ms (float)  -- Round-trip latency (-1 on failure).
              safe_to_trade (bool)    -- True if all metrics are within limits.
        """
        try:
            latency_ms = get_rpc_latency_ms(w3)
            gas_gwei = get_gas_price_gwei(w3)
            block_number = w3.eth.block_number
            chain_id = w3.eth.chain_id
            connected = True
        except Exception as exc:
            logger.error("System health check failed: %s", exc)
            return {
                "connected": False,
                "chain_id": None,
                "block_number": None,
                "gas_price_gwei": None,
                "rpc_latency_ms": -1.0,
                "safe_to_trade": False,
            }

        safe_to_trade = (
            connected
            and latency_ms >= 0
            and gas_gwei <= MAX_GAS_PRICE_GWEI
        )

        return {
            "connected": connected,
            "chain_id": chain_id,
            "block_number": block_number,
            "gas_price_gwei": gas_gwei,
            "rpc_latency_ms": latency_ms,
            "safe_to_trade": safe_to_trade,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

# Import with:  from core.safety import safety_controller
safety_controller = SafetyController()
