"""
tests/test_analyser.py
======================
Unit tests for core/analyser.py.

Covers the delta analysis engine that compares consecutive market snapshots.
No network access required — all tests use synthetic PoolData / MarketSnapshot
objects.

Run with:
  python tests/test_analyser.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.market_data import MarketSnapshot, PoolData
from core.analyser import (
    AnalysisResult,
    PoolDelta,
    analyse_cycle,
    detect_anomalies,
    get_pool_stability_score,
)


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


def _snapshot(pools, prices=None):
    return MarketSnapshot(
        pools=pools,
        prices=prices or {"BNB": 600.0},
    )


def _clean_delta(pool_address="0xA", symbol="USDT-WBNB",
                 apr_change_abs=0.0, apr_change_pct=0.0,
                 tvl_change_abs=0.0, tvl_change_pct=0.0,
                 volume_change_pct=0.0):
    """Return a non-anomalous PoolDelta for use in stability-score tests."""
    return PoolDelta(
        pool_address=pool_address,
        symbol=symbol,
        apr_change_abs=apr_change_abs,
        apr_change_pct=apr_change_pct,
        tvl_change_abs=tvl_change_abs,
        tvl_change_pct=tvl_change_pct,
        volume_change_pct=volume_change_pct,
    )


# ---------------------------------------------------------------------------
# analyse_cycle — first run (no previous snapshot)
# ---------------------------------------------------------------------------

def test_first_run_no_previous():
    """On the first cycle (previous=None), pool_deltas must be empty and
    significant_change=False."""
    current = _snapshot([_pool("0xA", apr=10.0)])
    result = analyse_cycle(current, previous=None)

    assert isinstance(result, AnalysisResult)
    assert result.pool_deltas == []
    assert result.significant_change is False
    assert result.first_run is True
    assert result.pools_new == 1   # current pool is "new" on first run
    assert result.pools_dropped == 0
    print("[PASS] analyse_cycle — first run produces empty pool_deltas")


# ---------------------------------------------------------------------------
# analyse_cycle — stable cycle (no significant change)
# ---------------------------------------------------------------------------

def test_stable_cycle_no_significant_change():
    """A tiny APR change (<0.5pp) must not set significant_change=True."""
    prev = _snapshot([_pool("0xA", apr=10.0, tvl=500_000)])
    curr = _snapshot([_pool("0xA", apr=10.1, tvl=500_000)])

    result = analyse_cycle(curr, prev)
    assert result.significant_change is False
    assert result.first_run is False

    # There should be exactly one PoolDelta for 0xA.
    delta = result.get_delta("0xA")
    assert delta is not None
    assert abs(delta.apr_change_abs - 0.1) < 0.001
    print("[PASS] analyse_cycle — 0.1pp APR change is not significant")


# ---------------------------------------------------------------------------
# analyse_cycle — significant change (≥0.5pp APR move)
# ---------------------------------------------------------------------------

def test_significant_change_large_apr_move():
    """A ≥0.5pp APR change on any clean pool must set significant_change=True."""
    prev = _snapshot([_pool("0xA", apr=10.0)])
    curr = _snapshot([_pool("0xA", apr=11.0)])  # +1pp

    result = analyse_cycle(curr, prev)
    assert result.significant_change is True
    print("[PASS] analyse_cycle — 1.0pp APR change sets significant_change=True")


# ---------------------------------------------------------------------------
# analyse_cycle — new and dropped pools
# ---------------------------------------------------------------------------

def test_new_pool_detected():
    """A pool present in current but not in previous must be counted in pools_new."""
    prev = _snapshot([_pool("0xA")])
    curr = _snapshot([_pool("0xA"), _pool("0xB")])

    result = analyse_cycle(curr, prev)
    assert result.pools_new == 1
    print("[PASS] analyse_cycle — new pool counted in pools_new")


def test_dropped_pool_detected():
    """A pool in previous but missing from current must be counted in pools_dropped."""
    prev = _snapshot([_pool("0xA"), _pool("0xB")])
    curr = _snapshot([_pool("0xA")])

    result = analyse_cycle(curr, prev)
    assert result.pools_dropped == 1
    print("[PASS] analyse_cycle — dropped pool counted in pools_dropped")


def test_new_pool_has_no_delta():
    """New pools cannot have a delta (no previous data to compare to)."""
    prev = _snapshot([_pool("0xA")])
    curr = _snapshot([_pool("0xA"), _pool("0xNew")])

    result = analyse_cycle(curr, prev)
    assert result.get_delta("0xNew") is None
    print("[PASS] analyse_cycle — new pool has no PoolDelta entry")


# ---------------------------------------------------------------------------
# detect_anomalies
# ---------------------------------------------------------------------------

def test_apr_spike_anomaly():
    """A >50% relative APR spike must produce a non-empty description list."""
    delta = _clean_delta(
        apr_change_abs=60.0,
        apr_change_pct=6.0,   # 600% relative (fraction form: 6.0 = 600%)
    )
    descriptions = detect_anomalies(delta)
    assert len(descriptions) > 0
    assert any("APR" in d for d in descriptions)
    print("[PASS] detect_anomalies — 600% relative APR spike flagged")


def test_tvl_drop_anomaly():
    """A >30% relative TVL drop must produce a non-empty description list."""
    delta = _clean_delta(
        tvl_change_abs=-400_000.0,
        tvl_change_pct=-0.4,   # -40% (fraction form: -0.4)
    )
    descriptions = detect_anomalies(delta)
    assert len(descriptions) > 0
    assert any("TVL" in d for d in descriptions)
    print("[PASS] detect_anomalies — 40% TVL drop flagged")


def test_no_anomaly_normal_change():
    """Normal, moderate changes must return an empty description list."""
    delta = _clean_delta(
        apr_change_abs=1.0,
        apr_change_pct=0.05,   # 5% relative — well below threshold
        tvl_change_abs=-10_000.0,
        tvl_change_pct=-0.02,  # 2% drop — well below threshold
        volume_change_pct=0.05,
    )
    descriptions = detect_anomalies(delta)
    assert descriptions == []
    print("[PASS] detect_anomalies — moderate changes return empty list")


# ---------------------------------------------------------------------------
# AnalysisResult.clean_pools()
# ---------------------------------------------------------------------------

def test_clean_pools_excludes_anomalous():
    """clean_pools() must exclude pool addresses marked anomalous."""
    pool_a = _pool("0xA", apr=10.0)
    pool_b = _pool("0xB", apr=20.0)

    prev = _snapshot([pool_a, pool_b])
    # Make pool_b spike massively to trigger anomaly detection.
    curr = _snapshot([pool_a, _pool("0xB", apr=200.0)])

    result = analyse_cycle(curr, prev)
    clean = result.clean_pools(curr.pools)

    clean_addresses = [p.pool for p in clean]
    assert "0xA" in clean_addresses
    assert "0xB" not in clean_addresses
    print("[PASS] AnalysisResult.clean_pools() — anomalous pool excluded")


# ---------------------------------------------------------------------------
# get_pool_stability_score
# ---------------------------------------------------------------------------

def test_stability_score_no_history_is_neutral():
    """With no history, stability score must be 0.5 (neutral)."""
    score = get_pool_stability_score("0xUnknown", delta_history=[])
    assert score == 0.5
    print("[PASS] get_pool_stability_score — no history returns 0.5")


def test_stability_score_stable_pool_high():
    """A pool with tiny APR changes should score > 0.5."""
    delta = _clean_delta(
        pool_address="0xStable",
        apr_change_abs=0.05,   # very small change
        apr_change_pct=0.001,
    )
    score = get_pool_stability_score("0xStable", delta_history=[delta])
    assert score > 0.5
    print(f"[PASS] get_pool_stability_score — stable pool scores {score:.3f} > 0.5")


def test_stability_score_volatile_pool_low():
    """A pool with large APR swings should score < 0.5."""
    delta = _clean_delta(
        pool_address="0xVol",
        apr_change_abs=8.0,   # large swing (8pp out of 10pp normaliser)
        apr_change_pct=0.8,
    )
    score = get_pool_stability_score("0xVol", delta_history=[delta])
    assert score < 0.5
    print(f"[PASS] get_pool_stability_score — volatile pool scores {score:.3f} < 0.5")


def test_stability_score_address_not_in_history():
    """A pool not present in delta_history must return 0.5."""
    delta = _clean_delta(
        pool_address="0xOther",
        apr_change_abs=5.0,
        apr_change_pct=0.25,
    )
    score = get_pool_stability_score("0xTarget", delta_history=[delta])
    assert score == 0.5
    print("[PASS] get_pool_stability_score — address absent from history returns 0.5")


# ---------------------------------------------------------------------------
# analyse_cycle — anomalous pool tracking
# ---------------------------------------------------------------------------

def test_anomalous_addresses_collected():
    """Pools that trigger detect_anomalies must appear in anomalous_addresses."""
    prev = _snapshot([_pool("0xSpike", apr=5.0)])
    curr = _snapshot([_pool("0xSpike", apr=500.0)])  # massive spike

    result = analyse_cycle(curr, prev)
    assert "0xSpike" in result.anomalous_addresses
    print("[PASS] analyse_cycle — anomalous pool address recorded in result")


def test_anomalous_pool_does_not_set_significant_change():
    """
    Anomalous pools must not set significant_change=True — the significance
    flag is reserved for clean (non-anomalous) pool changes only.
    """
    prev = _snapshot([_pool("0xSpike", apr=5.0)])
    curr = _snapshot([_pool("0xSpike", apr=500.0)])  # triggers anomaly

    result = analyse_cycle(curr, prev)
    # The pool is anomalous, so the large APR move must not mark significant.
    assert result.significant_change is False
    print("[PASS] analyse_cycle — anomalous pool APR change does not set significant_change")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    test_first_run_no_previous()
    test_stable_cycle_no_significant_change()
    test_significant_change_large_apr_move()
    test_new_pool_detected()
    test_dropped_pool_detected()
    test_new_pool_has_no_delta()
    test_apr_spike_anomaly()
    test_tvl_drop_anomaly()
    test_no_anomaly_normal_change()
    test_clean_pools_excludes_anomalous()
    test_stability_score_no_history_is_neutral()
    test_stability_score_stable_pool_high()
    test_stability_score_volatile_pool_low()
    test_stability_score_address_not_in_history()
    test_anomalous_addresses_collected()
    test_anomalous_pool_does_not_set_significant_change()

    print()
    print("All analyser tests passed.")
