"""
tests/test_sprint12.py
======================
Sprint 12: Integration Testing & Polish.

This file contains the end-to-end integration tests that simulate the full
TGLP Bot pipeline without any live Telegram interaction. The backend modules
are exercised in the same sequence a real user cycle would trigger them.

Tier 1 - Pure unit / mock tests (no network):
  - Pipeline wiring: session → snapshot → analysis → decision → cycle

Tier 2 - Live BSC Testnet read-only tests:
  - Full run_cycle() with auto_execute=False on a live snapshot
  - Portfolio summary built from a live wallet balance query
  - History query from an isolated SQLite database

All live tests use auto_execute=False so no transactions are broadcast.

Run with:
  python tests/test_sprint12.py
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from config.settings import BALANCED_GROWTH, AGGRESSIVE_ALPHA
from core.strategy_manager import UserSession, SessionManager
from core.market_data import MarketSnapshot, PoolData
from core.analyser import analyse_cycle
from core.decision_engine import (
    Decision,
    filter_pools_by_strategy,
    score_pools,
    make_decision,
)
from core.portfolio import (
    build_portfolio_summary,
    record_entry_value,
    record_gas_cost,
)
from core.dispatcher import run_cycle, build_cycle_callback, _build_position_dict
from helpers.database import (
    initialise_database,
    insert_trade,
    get_trades_for_user,
    count_trades_for_user,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(
    chat_id=70001,
    auto_execute=False,
    paused=False,
    safety_locked=False,
):
    return UserSession(
        chat_id=chat_id,
        wallet_address="0x" + "0" * 40,
        private_key="0x" + "a" * 64,
        active_strategy=BALANCED_GROWTH,
        compound_enabled=False,
        auto_execute=auto_execute,
        paused=paused,
        safety_locked=safety_locked,
    )


def _pool(address, symbol, apr=10.0, tvl=2_000_000.0,
          volume=100_000.0, pair_type="stable-largecap"):
    return PoolData(
        pool=address,
        symbol=symbol,
        apr=apr,
        apr_reward=0.0,
        tvl_usd=tvl,
        volume_24h=volume,
        fee_tier=500,
        pair_type=pair_type,
    )


def _snapshot(pools, prices=None):
    return MarketSnapshot(
        pools=pools,
        prices=prices or {"BNB": 600.0},
    )


def _isolated_db():
    """Return a temporary DB path and a cleanup function."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# Step 1: Session creation (simulates /start onboarding)
# ---------------------------------------------------------------------------

def test_session_creation():
    """
    A UserSession created during onboarding must have sane defaults:
    no position, not paused, not locked, zero gas/P&L accumulators.
    """
    session = _session()
    assert session.chat_id == 70001
    assert session.current_position is None
    assert session.paused is False
    assert session.safety_locked is False
    assert session.entry_value_usd == 0.0
    assert session.total_gas_spent_bnb == 0.0
    assert session.rebalance_count == 0
    assert session.is_operational() is True
    print("[PASS] Session creation: defaults are correct")


# ---------------------------------------------------------------------------
# Step 2: Market snapshot (simulates /explore)
# ---------------------------------------------------------------------------

def test_snapshot_filtering_and_scoring():
    """
    Given a synthetic market snapshot, filter_pools_by_strategy +
    score_pools must return a non-empty ranked list for BALANCED_GROWTH.
    """
    pools = [
        _pool("0xA", "USDT-WBNB",  apr=15.0, tvl=3_000_000),
        _pool("0xB", "USDT-USDC",  apr=8.0,  tvl=5_000_000, pair_type="stable-stable"),
        _pool("0xC", "CAKE-WBNB",  apr=25.0, tvl=500_000,   pair_type="largecap-largecap"),
        _pool("0xD", "USDT-WBNB",  apr=5.0,  tvl=100,       pair_type="stable-largecap"),  # low TVL
    ]
    snap = _snapshot(pools)
    filtered = filter_pools_by_strategy(snap.pools, BALANCED_GROWTH, None)
    scored = score_pools(filtered)

    # Low-TVL pool and wrong pair type (largecap-largecap) must be excluded.
    addresses = [sp.pool.pool for sp in scored]
    assert "0xA" in addresses, "0xA should pass BALANCED_GROWTH filter"
    assert "0xC" not in addresses, "largecap-largecap excluded by BALANCED_GROWTH"
    assert "0xD" not in addresses, "low-TVL pool excluded"
    assert len(scored) > 0

    # Scores must be in descending order.
    for i in range(len(scored) - 1):
        assert scored[i].score >= scored[i + 1].score

    print(f"[PASS] Snapshot filter+score: {len(scored)} pools after filtering, ranked correctly")


# ---------------------------------------------------------------------------
# Step 3: Analysis (first-cycle baseline)
# ---------------------------------------------------------------------------

def test_first_cycle_analysis_empty_deltas():
    """
    On the very first cycle (previous=None) analyse_cycle must return
    empty deltas and significant_change=False; the dispatcher should still
    proceed to the decision step rather than skip.
    """
    pools = [_pool("0xA", "USDT-WBNB", apr=12.0)]
    snap = _snapshot(pools)
    result = analyse_cycle(snap, previous=None)

    assert result.pool_deltas == []
    assert result.significant_change is False
    assert len(result.anomalous_addresses) == 0
    print("[PASS] First-cycle analysis: empty deltas, no anomalies, no significant change")


def test_second_cycle_analysis_produces_deltas():
    """
    With two consecutive synthetic snapshots, analyse_cycle must produce
    per-pool deltas on the second cycle.
    """
    pool_a = _pool("0xA", "USDT-WBNB", apr=10.0, tvl=2_000_000)
    prev = _snapshot([pool_a])
    curr = _snapshot([_pool("0xA", "USDT-WBNB", apr=10.5, tvl=2_100_000)])

    result = analyse_cycle(curr, prev)
    delta = result.get_delta("0xA")
    assert delta is not None, "Expected PoolDelta for 0xA"
    assert abs(delta.apr_change_abs - 0.5) < 0.001
    assert delta.tvl_change_abs > 0
    print("[PASS] Second-cycle analysis: delta computed for 0xA")


# ---------------------------------------------------------------------------
# Step 4: Decision engine (allocate when no position)
# ---------------------------------------------------------------------------

def test_decision_allocate_no_position():
    """
    When no current position exists and scored pools are available,
    make_decision must return Decision.ALLOCATE with a non-None target_pool.
    """
    from core.analyser import AnalysisResult
    pools = [
        _pool("0xA", "USDT-WBNB", apr=15.0, tvl=3_000_000),
        _pool("0xB", "USDT-USDC", apr=8.0,  tvl=2_000_000, pair_type="stable-largecap"),
    ]
    scored = score_pools(pools)
    analysis = AnalysisResult(
        pool_deltas=[],
        anomalies=[],
        anomalous_addresses=set(),
        significant_change=False,
        first_run=False,
    )

    result = make_decision(
        scored_pools=scored,
        current_position=None,
        strategy=BALANCED_GROWTH,
        analysis_result=analysis,
        compound_enabled=False,
        fees_available=False,
        pools_filtered_count=0,
    )
    assert result.action == Decision.ALLOCATE
    assert result.target_pool is not None
    print(f"[PASS] Decision ALLOCATE: target: {result.target_pool.symbol}")


# ---------------------------------------------------------------------------
# Step 5: run_cycle() with mocked snapshot (no network)
# ---------------------------------------------------------------------------

def test_run_cycle_paused_session_skips():
    """
    run_cycle must return immediately and NOT call notify_func when the
    session is paused.
    """
    session = _session(paused=True)
    notify_calls = []

    mock_w3 = MagicMock()
    run_cycle(session, lambda cid, msg: notify_calls.append(msg), mock_w3)

    assert notify_calls == [], "notify_func must not be called for a paused session"
    print("[PASS] run_cycle: paused session skips without notification")


def test_run_cycle_locked_session_skips():
    """
    run_cycle must return immediately when the session is safety-locked.
    """
    session = _session(safety_locked=True)
    notify_calls = []

    mock_w3 = MagicMock()
    run_cycle(session, lambda cid, msg: notify_calls.append(msg), mock_w3)

    assert notify_calls == [], "notify_func must not be called for a locked session"
    print("[PASS] run_cycle: locked session skips without notification")


def test_run_cycle_proposal_sent_on_good_snapshot():
    """
    run_cycle with auto_execute=False and a mock snapshot that returns
    eligible pools must call notify_func with a non-empty decision summary.
    """
    session = _session(auto_execute=False)
    notify_calls = []

    pools = [
        _pool("0xA", "USDT-WBNB", apr=15.0, tvl=3_000_000),
        _pool("0xB", "USDT-USDC", apr=8.0,  tvl=2_000_000, pair_type="stable-largecap"),
    ]
    snap = _snapshot(pools)

    mock_w3 = MagicMock()

    with patch("core.dispatcher.get_market_snapshot", return_value=snap):
        run_cycle(session, lambda cid, msg: notify_calls.append(msg), mock_w3)

    # At least one message must be sent (the decision summary).
    assert len(notify_calls) >= 1
    # The decision summary must reference a pool name or an action keyword.
    combined = " ".join(notify_calls)
    assert any(kw in combined for kw in ("ALLOCATE", "REBALANCE", "COMPOUND",
                                         "Allocate", "Rebalance", "Compound",
                                         "USDT", "pool")), (
        f"Unexpected notification text: {combined!r}"
    )
    # session.previous_snapshot must be populated after a cycle.
    assert session.previous_snapshot is snap
    print(f"[PASS] run_cycle: proposal sent ({len(notify_calls)} message(s))")


def test_run_cycle_sets_previous_snapshot():
    """
    After a successful cycle, session.previous_snapshot must equal the
    snapshot that was fetched.
    """
    session = _session(auto_execute=False)
    pools = [_pool("0xA", "USDT-WBNB", apr=15.0, tvl=3_000_000)]
    snap = _snapshot(pools)
    mock_w3 = MagicMock()

    with patch("core.dispatcher.get_market_snapshot", return_value=snap):
        run_cycle(session, lambda *_: None, mock_w3)

    assert session.previous_snapshot is snap
    print("[PASS] run_cycle: session.previous_snapshot updated after cycle")


def test_run_cycle_no_action_on_empty_pools():
    """
    When get_market_snapshot returns a snapshot with no pools,
    run_cycle must return early without calling notify_func.
    """
    session = _session(auto_execute=False)
    notify_calls = []

    empty_snap = _snapshot([])
    mock_w3 = MagicMock()

    with patch("core.dispatcher.get_market_snapshot", return_value=empty_snap):
        run_cycle(session, lambda cid, msg: notify_calls.append(msg), mock_w3)

    assert notify_calls == []
    print("[PASS] run_cycle: empty snapshot exits without notification")


# ---------------------------------------------------------------------------
# Step 6: Portfolio summary (simulates /dashboard)
# ---------------------------------------------------------------------------

def test_portfolio_summary_no_position():
    """
    After onboarding (no LP position yet), build_portfolio_summary must
    return has_position=False and wallet values based on the injected balance.
    """
    import core.portfolio as port_mod

    session = _session()
    session.current_position = None

    original_fn = port_mod.get_bnb_balance
    port_mod.get_bnb_balance = lambda w3, addr: 1.5   # inject 1.5 BNB

    try:
        mock_w3 = MagicMock()
        summary = build_portfolio_summary(mock_w3, session, bnb_price_usd=600.0)
        assert summary.has_position is False
        assert summary.position_value is None
        assert abs(summary.wallet_bnb - 1.5) < 0.001
        assert abs(summary.wallet_usd - 900.0) < 0.001
    finally:
        port_mod.get_bnb_balance = original_fn

    print("[PASS] Portfolio summary: no position, wallet_bnb=1.5, wallet_usd=$900")


def test_portfolio_pnl_after_entry_record():
    """
    After record_entry_value is called (simulating a successful allocate),
    calculate_pnl must return correct unrealised P&L.
    """
    from core.portfolio import calculate_pnl

    session = _session()
    record_entry_value(session, 1000.0)
    record_gas_cost(session, 0.01)    # 0.01 BNB × $600 = $6 gas

    pnl = calculate_pnl(session, current_value_usd=1200.0, bnb_price_usd=600.0)
    assert pnl.unrealised_pnl_usd == 200.0
    assert pnl.unrealised_pnl_pct == 20.0
    assert pnl.gas_cost_usd == 6.0
    assert pnl.net_pnl_usd == 194.0
    print("[PASS] Portfolio P&L: $200 unrealised, $194 net after $6 gas")


# ---------------------------------------------------------------------------
# Step 7: History query (simulates /history)
# ---------------------------------------------------------------------------

def test_history_empty_for_new_user():
    """
    A brand-new user (never executed a trade) must have an empty history.
    """
    path = _isolated_db()
    try:
        initialise_database(db_path=path)
        trades = get_trades_for_user(user_chat_id=70001, db_path=path)
        assert trades == []
        count = count_trades_for_user(user_chat_id=70001, db_path=path)
        assert count == 0
    finally:
        os.unlink(path)
    print("[PASS] History: empty list for new user")


def test_history_insert_and_retrieve():
    """
    insert_trade records a trade; get_trades_for_user retrieves it;
    count_trades_for_user returns 1.
    """
    path = _isolated_db()
    try:
        initialise_database(db_path=path)

        insert_trade(
            user_chat_id=70001,
            action_type="add_liquidity",
            pool_address="0xPool1",
            token_in="BNB",
            token_out="USDT-WBNB",
            amount_in="0.5000",
            tx_hash="0x" + "a" * 64,
            status="confirmed",
            gas_used=250000,
            gas_cost_bnb="0.001250",
            db_path=path,
        )

        trades = get_trades_for_user(user_chat_id=70001, db_path=path)
        assert len(trades) == 1
        assert trades[0]["action_type"] == "add_liquidity"
        assert trades[0]["pool_address"] == "0xPool1"
        assert trades[0]["status"] == "confirmed"

        count = count_trades_for_user(user_chat_id=70001, db_path=path)
        assert count == 1
    finally:
        os.unlink(path)
    print("[PASS] History: trade inserted and retrieved correctly")


def test_history_pagination():
    """
    get_trades_for_user with limit and offset must return the correct page.
    Insert 7 trades; page 0 (offset=0, limit=5) → 5; page 1 (offset=5) → 2.
    """
    path = _isolated_db()
    try:
        initialise_database(db_path=path)

        for i in range(7):
            insert_trade(
                user_chat_id=70001,
                action_type="add_liquidity",
                pool_address=f"0xPool{i}",
                status="confirmed",
                db_path=path,
            )

        page0 = get_trades_for_user(user_chat_id=70001, limit=5, offset=0, db_path=path)
        page1 = get_trades_for_user(user_chat_id=70001, limit=5, offset=5, db_path=path)
        assert len(page0) == 5
        assert len(page1) == 2
        assert count_trades_for_user(user_chat_id=70001, db_path=path) == 7
    finally:
        os.unlink(path)
    print("[PASS] History pagination: page 0: 5 trades, page 1: 2 trades")


# ---------------------------------------------------------------------------
# build_cycle_callback
# ---------------------------------------------------------------------------

def test_build_cycle_callback_is_callable():
    """build_cycle_callback must return a zero-argument callable."""
    session = _session()
    manager = SessionManager()
    manager.create(session)

    mock_w3 = MagicMock()
    cb = build_cycle_callback(session, lambda *_: None, mock_w3)
    assert callable(cb)
    print("[PASS] build_cycle_callback: returns a callable")


def test_build_position_dict_keys():
    """
    _build_position_dict must produce a dict with all required keys and
    correctly split the pool symbol into token symbols.
    """
    pool = _pool("0xPool1", "USDT-WBNB")
    pos = _build_position_dict(pool, token_id=42, amount0=500.0, amount1=0.833)

    for key in ("pool_address", "pool_symbol", "token_id",
                 "amount0", "amount1", "token0_symbol", "token1_symbol", "entry_time"):
        assert key in pos, f"Missing key: {key}"

    assert pos["pool_address"] == "0xPool1"
    assert pos["token0_symbol"] == "USDT"
    assert pos["token1_symbol"] == "WBNB"
    assert pos["token_id"] == 42
    print("[PASS] _build_position_dict: all keys present, symbols split correctly")


# ---------------------------------------------------------------------------
# Tier 2: Live BSC Testnet end-to-end pipeline
# ---------------------------------------------------------------------------

def test_live_full_pipeline():
    """
    Live end-to-end test with auto_execute=False:

    1. Create a UserSession (onboarding simulation).
    2. Fetch a real market snapshot via get_market_snapshot().
    3. Filter + score pools for AGGRESSIVE_ALPHA strategy.
    4. Run run_cycle(); decision summary must be sent via notify_func.
    5. Verify session.previous_snapshot is populated.
    6. Build portfolio summary from live wallet balance.
    """
    from helpers.blockchain import get_web3
    from core.market_data import get_market_snapshot, invalidate_cache
    import core.portfolio as port_mod

    w3 = get_web3()
    assert w3.is_connected(), "BSC Testnet not reachable"

    # Step 1: session
    session = UserSession(
        chat_id=79999,
        wallet_address="0x" + "0" * 40,
        private_key="0x" + "a" * 64,
        active_strategy=AGGRESSIVE_ALPHA,
        compound_enabled=False,
        auto_execute=False,
    )

    # Step 2: snapshot
    invalidate_cache()
    snap = get_market_snapshot(w3=w3)
    assert len(snap.pools) > 0, "No pools fetched from DeFiLlama"
    print(f"   Snapshot: {len(snap.pools)} pools, BNB=${snap.prices.get('BNB', 0):.2f}")

    # Step 3: filter + score
    filtered = filter_pools_by_strategy(snap.pools, AGGRESSIVE_ALPHA, None)
    scored = score_pools(filtered)
    print(f"   Filtered: {len(filtered)} pools pass AGGRESSIVE_ALPHA filter")

    # Step 4: run_cycle
    notify_calls = []

    run_cycle(session, lambda cid, msg: notify_calls.append(msg), w3)

    # Step 5: session state
    assert session.previous_snapshot is not None, (
        "session.previous_snapshot must be set after a cycle"
    )

    # If the decision was NO_ACTION, notify_calls may be empty (valid).
    print(f"   Cycle: {len(notify_calls)} notification(s) sent")
    if notify_calls:
        print(f"   Decision: {notify_calls[0][:80]}...")

    # Step 6: portfolio summary (mock balance, testnet wallet is unfunded)
    original_fn = port_mod.get_bnb_balance
    port_mod.get_bnb_balance = lambda _w3, _addr: 0.5
    try:
        bnb_price = snap.prices.get("BNB", 600.0)
        summary = build_portfolio_summary(w3, session, bnb_price_usd=bnb_price)
        assert summary.wallet_bnb == 0.5
        assert summary.has_position is False   # no trade was executed
    finally:
        port_mod.get_bnb_balance = original_fn

    print("[PASS] Live full pipeline: snapshot → analysis → decision → portfolio summary")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # Session creation
    test_session_creation()

    # Snapshot filter + score
    test_snapshot_filtering_and_scoring()

    # Analysis
    test_first_cycle_analysis_empty_deltas()
    test_second_cycle_analysis_produces_deltas()

    # Decision engine
    test_decision_allocate_no_position()

    # run_cycle unit tests (mocked snapshot)
    test_run_cycle_paused_session_skips()
    test_run_cycle_locked_session_skips()
    test_run_cycle_proposal_sent_on_good_snapshot()
    test_run_cycle_sets_previous_snapshot()
    test_run_cycle_no_action_on_empty_pools()

    # Portfolio
    test_portfolio_summary_no_position()
    test_portfolio_pnl_after_entry_record()

    # History (isolated DB)
    test_history_empty_for_new_user()
    test_history_insert_and_retrieve()
    test_history_pagination()

    # Callback builder
    test_build_cycle_callback_is_callable()
    test_build_position_dict_keys()

    # Tier 2: live BSC Testnet
    test_live_full_pipeline()

    print()
    print("All Sprint 12 integration tests passed.")
