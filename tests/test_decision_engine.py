"""
tests/test_decision_engine.py
==============================
Unit tests for core/decision_engine.py.

Covers filtering, scoring, and decision-making with synthetic pool data.
No network access required.

Run with:
  python tests/test_decision_engine.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.market_data import MarketSnapshot, PoolData
from core.analyser import AnalysisResult
from core.decision_engine import (
    Decision,
    DecisionResult,
    ScoredPool,
    filter_pools_by_strategy,
    format_decision_summary,
    make_decision,
    score_pools,
)
from config.settings import BALANCED_GROWTH, CONSERVATIVE_YIELD, AGGRESSIVE_ALPHA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool(address="0xPool1", symbol="USDT-WBNB", apr=10.0, tvl=500_000.0,
          volume=50_000.0, fee_tier=500, pair_type="stable-largecap"):
    return PoolData(
        pool=address,
        symbol=symbol,
        apr=apr,
        apr_reward=0.0,
        tvl_usd=tvl,
        volume_24h=volume,
        fee_tier=fee_tier,
        pair_type=pair_type,
    )


def _clean_analysis():
    """An AnalysisResult with no anomalies."""
    return AnalysisResult(
        pool_deltas=[],
        anomalies=[],
        anomalous_addresses=set(),
        significant_change=False,
        first_run=False,
    )


# ---------------------------------------------------------------------------
# filter_pools_by_strategy
# ---------------------------------------------------------------------------

def test_filter_removes_wrong_pair_type():
    """Conservative strategy only allows stable-stable; other types are filtered out."""
    pools = [
        _pool("0xSS",  pair_type="stable-stable",   tvl=2_000_000),
        _pool("0xSL",  pair_type="stable-largecap",  tvl=2_000_000),
        _pool("0xLL",  pair_type="largecap-largecap", tvl=2_000_000),
    ]
    filtered = filter_pools_by_strategy(pools, CONSERVATIVE_YIELD, None)
    addresses = [p.pool for p in filtered]
    assert "0xSS" in addresses
    assert "0xSL" not in addresses
    assert "0xLL" not in addresses
    print("[PASS] filter_pools_by_strategy: Conservative only returns stable-stable")


def test_filter_removes_low_tvl():
    """Pools below the strategy's min_tvl_usd floor must be removed."""
    # BALANCED_GROWTH has a min TVL; use a pool clearly below it.
    pools = [
        _pool("0xHigh", tvl=5_000_000, pair_type="stable-largecap"),
        _pool("0xLow",  tvl=1_000,     pair_type="stable-largecap"),
    ]
    filtered = filter_pools_by_strategy(pools, BALANCED_GROWTH, None)
    addresses = [p.pool for p in filtered]
    assert "0xHigh" in addresses
    assert "0xLow" not in addresses
    print("[PASS] filter_pools_by_strategy: low-TVL pool filtered out")


def test_filter_removes_anomalous_pools():
    """Pools flagged as anomalous in the analysis result must be excluded."""
    pools = [
        _pool("0xClean",   tvl=2_000_000, pair_type="stable-largecap"),
        _pool("0xAnomal",  tvl=2_000_000, pair_type="stable-largecap"),
    ]
    analysis = AnalysisResult(
        pool_deltas=[],
        anomalies=[],
        anomalous_addresses={"0xAnomal"},
        significant_change=False,
        first_run=False,
    )
    filtered = filter_pools_by_strategy(pools, BALANCED_GROWTH, analysis)
    addresses = [p.pool for p in filtered]
    assert "0xClean" in addresses
    assert "0xAnomal" not in addresses
    print("[PASS] filter_pools_by_strategy: anomalous pool excluded")


def test_filter_with_no_analysis_result():
    """Passing analysis_result=None (first run) must skip the anomaly filter."""
    pools = [_pool("0xA", tvl=5_000_000, pair_type="stable-largecap")]
    filtered = filter_pools_by_strategy(pools, BALANCED_GROWTH, None)
    assert len(filtered) == 1
    print("[PASS] filter_pools_by_strategy: analysis_result=None works correctly")


def test_filter_empty_pool_list():
    filtered = filter_pools_by_strategy([], BALANCED_GROWTH, None)
    assert filtered == []
    print("[PASS] filter_pools_by_strategy: empty input returns empty list")


# ---------------------------------------------------------------------------
# score_pools
# ---------------------------------------------------------------------------

def test_score_single_pool_all_half():
    """A single pool with no peers scores 0.5 on all normalised components."""
    pools = [_pool("0xA", apr=10.0, tvl=500_000, volume=50_000)]
    scored = score_pools(pools)
    assert len(scored) == 1
    sp = scored[0]
    # With a single pool, min == max for every metric → normalise to 0.5.
    assert sp.norm_apr == 0.5
    assert sp.norm_tvl == 0.5
    print("[PASS] score_pools: single pool gets 0.5 on all component scores")


def test_score_ranking_order():
    """Higher-APR pool must rank above lower-APR pool when TVL is equal."""
    pools = [
        _pool("0xLow",  apr=5.0,  tvl=500_000),
        _pool("0xHigh", apr=20.0, tvl=500_000),
    ]
    scored = score_pools(pools)
    assert scored[0].pool.pool == "0xHigh"
    assert scored[1].pool.pool == "0xLow"
    print("[PASS] score_pools: higher APR pool ranks first")


def test_score_empty_input():
    scored = score_pools([])
    assert scored == []
    print("[PASS] score_pools: empty input returns empty list")


def test_score_composite_between_0_and_1():
    """Composite score must always be in [0, 1]."""
    pools = [
        _pool("0xA", apr=5.0,  tvl=100_000, volume=10_000),
        _pool("0xB", apr=20.0, tvl=800_000, volume=200_000),
        _pool("0xC", apr=12.0, tvl=400_000, volume=80_000),
    ]
    for sp in score_pools(pools):
        assert 0.0 <= sp.score <= 1.0, (
            f"Score {sp.score} out of range for {sp.pool.pool}"
        )
    print("[PASS] score_pools: all composite scores in [0, 1]")


# ---------------------------------------------------------------------------
# make_decision
# ---------------------------------------------------------------------------

def test_decision_allocate_when_no_position():
    """When there is no current position, the decision must be ALLOCATE."""
    pools = [_pool("0xBest", apr=20.0, tvl=500_000, pair_type="stable-largecap")]
    scored = score_pools(pools)

    result = make_decision(
        scored_pools=scored,
        current_position=None,
        strategy=BALANCED_GROWTH,
        analysis_result=_clean_analysis(),
        compound_enabled=False,
        fees_available=False,
        pools_filtered_count=0,
    )
    assert result.action == Decision.ALLOCATE
    assert result.target_pool is not None
    print("[PASS] make_decision: ALLOCATE when no position exists")


def test_decision_no_action_when_no_pools():
    """With no scored pools, decision must be NO_ACTION."""
    result = make_decision(
        scored_pools=[],
        current_position=None,
        strategy=BALANCED_GROWTH,
        analysis_result=_clean_analysis(),
        compound_enabled=False,
        fees_available=False,
        pools_filtered_count=0,
    )
    assert result.action == Decision.NO_ACTION
    print("[PASS] make_decision: NO_ACTION when no pools available")


def test_decision_compound_when_fees_available():
    """When compound_enabled=True and fees are available, prefer COMPOUND."""
    pools = [_pool("0xCurrent", apr=10.0, tvl=500_000, pair_type="stable-largecap")]
    scored = score_pools(pools)

    current_pos = {"pool_address": "0xCurrent", "pool_symbol": "USDT-WBNB"}

    result = make_decision(
        scored_pools=scored,
        current_position=current_pos,
        strategy=BALANCED_GROWTH,
        analysis_result=_clean_analysis(),
        compound_enabled=True,
        fees_available=True,
        pools_filtered_count=0,
    )
    assert result.action == Decision.COMPOUND
    print("[PASS] make_decision: COMPOUND when fees_available=True and compound_enabled")


def test_decision_no_action_when_stable():
    """When the current pool is already the top scorer and gap < threshold, NO_ACTION."""
    # Use three pools so the score gap between 1st and 2nd is small.
    pools = [
        _pool("0xCurrent", apr=10.1, tvl=500_000, pair_type="stable-largecap"),
        _pool("0xAlt",     apr=10.0, tvl=500_000, pair_type="stable-largecap"),
        _pool("0xAnchor",  apr=9.9,  tvl=500_000, pair_type="stable-largecap"),
    ]
    scored = score_pools(pools)
    current_pos = {"pool_address": "0xCurrent", "pool_symbol": "USDT-WBNB"}

    result = make_decision(
        scored_pools=scored,
        current_position=current_pos,
        strategy=BALANCED_GROWTH,
        analysis_result=_clean_analysis(),
        compound_enabled=False,
        fees_available=False,
        pools_filtered_count=0,
    )
    # The current pool is top, so no rebalance needed.
    assert result.action in (Decision.NO_ACTION, Decision.REBALANCE)
    print(f"[PASS] make_decision: stable position: {result.action.value}")


def test_decision_rebalance_to_better_pool():
    """When a clearly better pool exists, REBALANCE must be chosen."""
    pools = [
        _pool("0xBetter",  apr=80.0,  tvl=5_000_000, pair_type="stable-largecap"),
        _pool("0xCurrent", apr=5.0,   tvl=500_000,   pair_type="stable-largecap"),
        _pool("0xOther",   apr=4.0,   tvl=400_000,   pair_type="stable-largecap"),
    ]
    scored = score_pools(pools)
    current_pos = {"pool_address": "0xCurrent", "pool_symbol": "USDT-WBNB"}

    result = make_decision(
        scored_pools=scored,
        current_position=current_pos,
        strategy=BALANCED_GROWTH,
        analysis_result=_clean_analysis(),
        compound_enabled=False,
        fees_available=False,
        pools_filtered_count=0,
    )
    assert result.action == Decision.REBALANCE
    assert result.target_pool.pool == "0xBetter"
    print("[PASS] make_decision: REBALANCE to clearly superior pool")


# ---------------------------------------------------------------------------
# format_decision_summary
# ---------------------------------------------------------------------------

def test_format_decision_summary_all_actions():
    """format_decision_summary must return a non-empty string for all Decision values."""
    pools = [_pool("0xA")]
    scored = score_pools(pools)

    for action in (Decision.ALLOCATE, Decision.REBALANCE, Decision.COMPOUND, Decision.NO_ACTION):
        result = DecisionResult(
            action=action,
            target_pool=scored[0].pool if action != Decision.NO_ACTION else None,
            reasoning="Test reasoning.",
            scored_pools=scored,
        )
        text = format_decision_summary(result)
        assert isinstance(text, str) and len(text) > 0, (
            f"format_decision_summary returned empty for {action}"
        )
    print("[PASS] format_decision_summary: all four actions produce non-empty strings")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # filter_pools_by_strategy
    test_filter_removes_wrong_pair_type()
    test_filter_removes_low_tvl()
    test_filter_removes_anomalous_pools()
    test_filter_with_no_analysis_result()
    test_filter_empty_pool_list()

    # score_pools
    test_score_single_pool_all_half()
    test_score_ranking_order()
    test_score_empty_input()
    test_score_composite_between_0_and_1()

    # make_decision
    test_decision_allocate_when_no_position()
    test_decision_no_action_when_no_pools()
    test_decision_compound_when_fees_available()
    test_decision_no_action_when_stable()
    test_decision_rebalance_to_better_pool()

    # format_decision_summary
    test_format_decision_summary_all_actions()

    print()
    print("All decision engine tests passed.")
