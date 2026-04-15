"""
core/dispatcher.py
==================
Analysis-decision-execution pipeline for TGLP Bot.

The dispatcher is the brain of the automation. It is called by the scheduler
once per CYCLE_INTERVAL_SECONDS for each active user and orchestrates every
other core module in sequence:

    market_data → analyser → alerts → safety → decision_engine → executor

Design principles:
  - run_cycle() is a regular synchronous function, safe to call from
    APScheduler's background thread.
  - All Telegram output is routed through the notify_func callback so the
    dispatcher has no direct dependency on python-telegram-bot.
  - Every on-chain action is gated by run_pre_execution_checks(). If the
    check fails, the action is skipped and the user is notified.
  - On execution success, session state (current_position, gas totals,
    rebalance_count, entry_value_usd) is updated immediately.
"""

import logging
import time
from typing import Any, Callable, Optional

from web3 import Web3

from config.settings import CYCLE_TIMEOUT_WARNING_SECONDS, MIN_BNB_FOR_GAS
from core.alerts import check_all_alerts, format_alert_message
from core.analyser import analyse_cycle
from core.decision_engine import (
    Decision,
    filter_pools_by_strategy,
    format_decision_summary,
    make_decision,
    score_pools,
)
from core.executor import (
    execute_allocate,
    execute_compound,
    execute_rebalance,
)
from core.market_data import get_market_snapshot
from core.portfolio import estimate_position_value, record_entry_value, record_gas_cost
from core.safety import safety_controller
from helpers.blockchain import get_bnb_balance
from helpers.database import insert_log, insert_trade

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position dict builder
# ---------------------------------------------------------------------------

def _build_position_dict(
    pool_data: Any,
    token_id: Optional[int],
    amount0: float,
    amount1: float,
) -> dict:
    """
    Build the current_position dict stored on UserSession.

    The dict must contain at minimum 'pool_address' (used by make_decision
    to locate the current pool in the scored list) and 'token_id' (used by
    execute_rebalance and execute_compound).

    Token symbols are derived by splitting pool_data.symbol on '-'.
    e.g. "USDT-USDC" → token0_symbol="USDT", token1_symbol="USDC".

    Args:
        pool_data: The PoolData for the pool just entered.
        token_id:  NFT token ID from the PositionManager (may be None).
        amount0:   Human-readable amount of token0 deposited.
        amount1:   Human-readable amount of token1 deposited.

    Returns:
        A dict suitable for assignment to session.current_position.
    """
    parts = pool_data.symbol.split("-", 1)
    sym0 = parts[0] if len(parts) > 0 else ""
    sym1 = parts[1] if len(parts) > 1 else ""

    return {
        "pool_address": pool_data.pool,
        "pool_symbol": pool_data.symbol,
        "token_id": token_id,
        "amount0": amount0,
        "amount1": amount1,
        "token0_symbol": sym0,
        "token1_symbol": sym1,
        "entry_time": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _handle_allocate(session: Any, decision: Any, snapshot: Any, w3: Web3) -> Optional[str]:
    """
    Execute an ALLOCATE decision and update session state on success.

    Returns a human-readable status string for the Telegram notification,
    or None if the action was blocked by safety checks.
    """
    pool_data = decision.target_pool

    # Pre-execution safety check.
    wallet_bnb = get_bnb_balance(w3, session.wallet_address)
    amount_bnb = max(wallet_bnb - MIN_BNB_FOR_GAS, 0.0)

    if amount_bnb <= 0:
        return (
            f"Allocation skipped: wallet balance {wallet_bnb:.4f} BNB is "
            f"below the gas reserve ({MIN_BNB_FOR_GAS} BNB)."
        )

    safety = safety_controller.run_pre_execution_checks(
        w3, session, amount_bnb, wallet_balance_bnb=wallet_bnb
    )
    if not safety.passed:
        return f"Allocation blocked by safety check ({safety.check_name}): {safety.reason}"

    result = execute_allocate(
        w3=w3,
        pool_data=pool_data,
        amount_bnb=amount_bnb,
        wallet_address=session.wallet_address,
        private_key=session.private_key,
        strategy=session.active_strategy,
    )

    if result.success:
        session.current_position = _build_position_dict(
            pool_data, result.token_id, result.amount0, result.amount1
        )
        session.position_token_id = result.token_id

        bnb_price = snapshot.prices.get("BNB", 0.0)
        pos_val = estimate_position_value(session.current_position, bnb_price)
        record_entry_value(session, pos_val.value_usd)
        record_gas_cost(session, result.gas_cost_bnb)

        insert_trade(
            user_chat_id=session.chat_id,
            action_type="add_liquidity",
            pool_address=pool_data.pool,
            token_in="BNB",
            token_out=pool_data.symbol,
            amount_in=f"{amount_bnb:.4f}",
            tx_hash=result.tx_hashes[0] if result.tx_hashes else None,
            status="confirmed",
            gas_used=result.gas_used,
            gas_cost_bnb=f"{result.gas_cost_bnb:.6f}",
        )

        insert_log("INFO", f"Allocated {amount_bnb:.4f} BNB to {pool_data.symbol}",
                   context=f"chat_id={session.chat_id}")
        return (
            f"Allocated {amount_bnb:.4f} BNB to {pool_data.symbol}.\n"
            f"Token ID: {result.token_id} | "
            f"Gas: {result.gas_cost_bnb:.6f} BNB"
        )
    else:
        insert_log("ERROR", f"Allocation failed: {result.error}",
                   context=f"chat_id={session.chat_id}")
        return f"Allocation failed: {result.error}"


def _handle_rebalance(session: Any, decision: Any, snapshot: Any, w3: Web3) -> Optional[str]:
    """
    Execute a REBALANCE decision and update session state on success.
    """
    if session.position_token_id is None:
        return "Rebalance skipped: no token ID on session."

    new_pool = decision.target_pool
    wallet_bnb = get_bnb_balance(w3, session.wallet_address)
    # After removing liquidity, we'll have ~wallet_bnb worth to reallocate.
    amount_bnb = max(wallet_bnb - MIN_BNB_FOR_GAS, 0.0)

    safety = safety_controller.run_pre_execution_checks(
        w3, session, amount_bnb, wallet_balance_bnb=wallet_bnb
    )
    if not safety.passed:
        return f"Rebalance blocked by safety check ({safety.check_name}): {safety.reason}"

    old_symbol = (
        session.current_position.get("pool_symbol", "current pool")
        if session.current_position else "current pool"
    )

    result = execute_rebalance(
        w3=w3,
        token_id=session.position_token_id,
        new_pool_data=new_pool,
        amount_bnb=amount_bnb,
        wallet_address=session.wallet_address,
        private_key=session.private_key,
        strategy=session.active_strategy,
    )

    if result.success:
        session.current_position = _build_position_dict(
            new_pool, result.token_id, result.amount0, result.amount1
        )
        session.position_token_id = result.token_id
        session.rebalance_count += 1

        bnb_price = snapshot.prices.get("BNB", 0.0)
        pos_val = estimate_position_value(session.current_position, bnb_price)
        record_entry_value(session, pos_val.value_usd)
        record_gas_cost(session, result.gas_cost_bnb)

        insert_trade(
            user_chat_id=session.chat_id,
            action_type="remove_liquidity",
            pool_address=new_pool.pool,
            tx_hash=result.tx_hashes[0] if result.tx_hashes else None,
            status="confirmed",
            gas_used=result.gas_used,
            gas_cost_bnb=f"{result.gas_cost_bnb:.6f}",
        )
        insert_log("INFO",
                   f"Rebalanced from {old_symbol} to {new_pool.symbol}",
                   context=f"chat_id={session.chat_id}")
        return (
            f"Rebalanced from {old_symbol} to {new_pool.symbol}.\n"
            f"Token ID: {result.token_id} | "
            f"Gas: {result.gas_cost_bnb:.6f} BNB"
        )
    else:
        insert_log("ERROR", f"Rebalance failed: {result.error}",
                   context=f"chat_id={session.chat_id}")
        return f"Rebalance failed: {result.error}"


def _handle_compound(session: Any, decision: Any, snapshot: Any, w3: Web3) -> Optional[str]:
    """
    Execute a COMPOUND decision and update gas records on success.
    """
    if session.position_token_id is None:
        return "Compound skipped: no token ID on session."

    current_pos = session.current_position or {}
    pool_address = current_pos.get("pool_address", "")
    pool_data = snapshot.get_pool(pool_address) if pool_address else None

    if pool_data is None:
        return "Compound skipped: current pool not in market snapshot."

    result = execute_compound(
        w3=w3,
        token_id=session.position_token_id,
        pool_data=pool_data,
        wallet_address=session.wallet_address,
        private_key=session.private_key,
        strategy=session.active_strategy,
    )

    if result.success:
        record_gas_cost(session, result.gas_cost_bnb)
        insert_trade(
            user_chat_id=session.chat_id,
            action_type="compound",
            pool_address=pool_data.pool,
            tx_hash=result.tx_hashes[0] if result.tx_hashes else None,
            status="confirmed",
            gas_used=result.gas_used,
            gas_cost_bnb=f"{result.gas_cost_bnb:.6f}",
        )
        insert_log("INFO", f"Compounded fees for {pool_data.symbol}",
                   context=f"chat_id={session.chat_id}")
        return (
            f"Compounded fees for {pool_data.symbol}.\n"
            f"Fees collected: {result.fees0_collected:.4f} / "
            f"{result.fees1_collected:.4f} | "
            f"Gas: {result.gas_cost_bnb:.6f} BNB"
        )
    else:
        return f"Compound failed: {result.error}"


# ---------------------------------------------------------------------------
# Main cycle entry point
# ---------------------------------------------------------------------------

def run_cycle(
    session: Any,
    notify_func: Callable[[int, str], None],
    w3: Web3,
) -> None:
    """
    Run one complete analysis-decision-execution cycle for a user.

    This is the function the scheduler calls every CYCLE_INTERVAL_SECONDS.
    It is safe to call from a background thread.

    Pipeline:
      1. Operational check  -- skip if session is paused or safety-locked.
      2. Market snapshot    -- fetch (cached up to 30s).
      3. Analysis           -- compute pool deltas vs previous snapshot.
      4. Alert check        -- notify on any triggered watchlist thresholds.
      5. Anomaly escalation -- safety lock after SAFETY_ANOMALY_LOCK_THRESHOLD
                               consecutive bad-data cycles.
      6. Decision           -- filter → score → make_decision.
      7. Execute / propose  -- if auto_execute: run safety + execute;
                               else: send decision summary to user.
      8. Timing warning     -- log if the cycle exceeded CYCLE_TIMEOUT_WARNING_SECONDS.

    Args:
        session:     UserSession for the user whose cycle is running.
        notify_func: Callback that sends a Telegram message.
                     Signature: notify_func(chat_id: int, text: str) -> None.
                     The dispatcher never calls the Telegram API directly.
        w3:          Connected Web3 instance. Created once by app.py and
                     shared across all cycles.
    """
    cycle_start = time.monotonic()
    chat_id = session.chat_id

    # ── Step 1: Operational check ─────────────────────────────────────────
    if not session.is_operational():
        logger.debug("Cycle skipped for chat_id %d (paused or locked).", chat_id)
        return

    logger.debug("Cycle start for chat_id %d.", chat_id)

    # ── Step 2: Market snapshot ───────────────────────────────────────────
    snapshot = get_market_snapshot(w3=w3)
    if not snapshot.pools:
        logger.warning("Cycle: empty snapshot for chat_id %d -- skipping.", chat_id)
        return

    # ── Step 3: Analysis ──────────────────────────────────────────────────
    # session.previous_snapshot may be None (first run) or a prior snapshot.
    analysis = analyse_cycle(snapshot, session.previous_snapshot)
    session.previous_snapshot = snapshot

    # ── Step 4: Alert check ───────────────────────────────────────────────
    alerts = check_all_alerts(session, snapshot, snapshot.prices)
    for alert in alerts:
        notify_func(chat_id, format_alert_message(alert))

    # ── Step 5: Anomaly escalation ────────────────────────────────────────
    has_anomalies = len(analysis.anomalous_addresses) > 0
    safety_result = safety_controller.record_anomaly_cycle(session, has_anomalies)
    if not safety_result.passed:
        # Safety lock was just engaged this cycle.
        msg = f"Safety lock engaged after repeated anomalies.\n{safety_result.reason}"
        notify_func(chat_id, msg)
        logger.error("Safety lock engaged for chat_id %d: %s", chat_id, safety_result.reason)
        return

    # ── Step 6: Decision ──────────────────────────────────────────────────
    original_count = len(snapshot.pools)
    filtered = filter_pools_by_strategy(
        snapshot.pools, session.active_strategy, analysis
    )
    filtered_count = original_count - len(filtered)
    scored = score_pools(filtered)

    decision = make_decision(
        scored_pools=scored,
        current_position=session.current_position,
        strategy=session.active_strategy,
        analysis_result=analysis,
        compound_enabled=session.compound_enabled,
        fees_available=session.has_position(),
        pools_filtered_count=filtered_count,
    )

    logger.info(
        "Decision for chat_id %d: %s (target: %s)",
        chat_id,
        decision.action.value,
        decision.target_pool.symbol if decision.target_pool else "none",
    )

    if decision.action == Decision.NO_ACTION:
        logger.debug("NO_ACTION for chat_id %d: %s", chat_id, decision.reasoning)
        return

    # ── Step 7a: Auto-execute path ────────────────────────────────────────
    if session.auto_execute:
        if decision.action == Decision.ALLOCATE:
            msg = _handle_allocate(session, decision, snapshot, w3)
        elif decision.action == Decision.REBALANCE:
            msg = _handle_rebalance(session, decision, snapshot, w3)
        elif decision.action == Decision.COMPOUND:
            msg = _handle_compound(session, decision, snapshot, w3)
        else:
            msg = None

        if msg:
            notify_func(chat_id, msg)

    # ── Step 7b: Proposal path (user confirmation required) ───────────────
    else:
        summary = format_decision_summary(decision)
        notify_func(chat_id, summary)

    # ── Step 8: Cycle timing ──────────────────────────────────────────────
    elapsed = time.monotonic() - cycle_start
    if elapsed > CYCLE_TIMEOUT_WARNING_SECONDS:
        logger.warning(
            "Cycle for chat_id %d took %.1fs (warning threshold: %ds).",
            chat_id, elapsed, CYCLE_TIMEOUT_WARNING_SECONDS,
        )
    else:
        logger.debug("Cycle for chat_id %d completed in %.2fs.", chat_id, elapsed)


# ---------------------------------------------------------------------------
# Scheduler callback builder
# ---------------------------------------------------------------------------

def build_cycle_callback(
    session: Any,
    notify_func: Callable[[int, str], None],
    w3: Web3,
) -> Callable:
    """
    Return a zero-argument callable suitable for passing to
    BotScheduler.add_user_job().

    The returned function looks up the current session from session_manager
    at call time rather than capturing the session object directly.  This
    ensures that session mutations (position state, flags) made between cycles
    are visible to each subsequent cycle without re-registering the job.

    Args:
        session:     The initial UserSession, used only to extract chat_id.
        notify_func: Telegram notification callback.
        w3:          Connected Web3 instance shared across all cycles.

    Returns:
        A no-argument callable for the scheduler.
    """
    from core.strategy_manager import session_manager

    chat_id = session.chat_id

    def _callback() -> None:
        current_session = session_manager.get(chat_id)
        if current_session is None:
            logger.warning(
                "Cycle fired for chat_id %d but session no longer exists.",
                chat_id,
            )
            return
        try:
            run_cycle(current_session, notify_func, w3)
        except Exception as exc:
            logger.exception(
                "Unhandled exception in cycle for chat_id %d: %s", chat_id, exc
            )

    return _callback
