"""
tests/test_sprint5.py
=====================
Sprint 5 verification tests for core/analyser.py.

Run with:
  python tests/test_sprint5.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers: build mock snapshots
# ---------------------------------------------------------------------------

def _make_pool(address, symbol, apr, apr_reward=0.0, tvl=500_000, volume=100_000,
               pair_type="stable-stable", sqrt_price=None):
    from core.market_data import PoolData
    p = PoolData(
        pool=address, symbol=symbol,
        apr=apr, apr_reward=apr_reward,
        tvl_usd=tvl, volume_24h=volume,
        fee_tier=100, pair_type=pair_type,
    )
    if sqrt_price is not None:
        p.sqrt_price_x96 = sqrt_price
        p.chain_data_available = True
    return p


def _make_snapshot(pools, prices=None):
    from core.market_data import MarketSnapshot
    return MarketSnapshot(
        pools=pools,
        prices=prices or {"BNB": 320.0, "USDT": 1.0},
    )


# ---------------------------------------------------------------------------
# Test 1: first run (no previous snapshot)
# ---------------------------------------------------------------------------

def test_first_run():
    from core.analyser import analyse_cycle

    snap = _make_snapshot([
        _make_pool("0xA", "USDT-USDC", apr=5.0),
        _make_pool("0xB", "BNB-USDT", apr=12.0),
    ])

    result = analyse_cycle(current=snap, previous=None)

    assert result.first_run is True
    assert result.significant_change is False
    assert result.pool_deltas == []
    assert result.anomalies == []
    assert len(result.anomalous_addresses) == 0
    assert result.pools_new == 2
    assert result.pools_dropped == 0
    assert result.pools_compared == 0

    print("[PASS] analyse_cycle() — first run")


# ---------------------------------------------------------------------------
# Test 2: stable cycle (no significant change)
# ---------------------------------------------------------------------------

def test_stable_cycle():
    from core.analyser import analyse_cycle

    pool_a = _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=1_000_000, volume=200_000)
    pool_b = _make_pool("0xB", "BNB-USDT",  apr=12.0, tvl=2_000_000, volume=500_000)
    prev = _make_snapshot([pool_a, pool_b])

    # Tiny changes well below thresholds
    pool_a2 = _make_pool("0xA", "USDT-USDC", apr=5.1, tvl=1_001_000, volume=200_500)
    pool_b2 = _make_pool("0xB", "BNB-USDT",  apr=12.1, tvl=2_002_000, volume=500_100)
    curr = _make_snapshot([pool_a2, pool_b2])

    result = analyse_cycle(current=curr, previous=prev)

    assert result.first_run is False
    assert result.pools_compared == 2
    # APR change is 0.1pp < 0.5pp threshold → not significant
    assert result.significant_change is False
    assert len(result.anomalies) == 0
    assert len(result.anomalous_addresses) == 0
    assert len(result.pool_deltas) == 2

    # Check delta values for pool A
    delta_a = result.get_delta("0xA")
    assert delta_a is not None
    assert abs(delta_a.apr_change_abs - 0.1) < 1e-9
    assert delta_a.tvl_change_abs == 1_000.0
    assert not delta_a.is_anomalous

    print("[PASS] analyse_cycle() — stable cycle, no significant change")


# ---------------------------------------------------------------------------
# Test 3: significant change (APR moved above threshold)
# ---------------------------------------------------------------------------

def test_significant_change():
    from core.analyser import analyse_cycle

    prev = _make_snapshot([_make_pool("0xA", "BNB-USDT", apr=10.0)])
    # APR increases by 2pp → above 0.5pp threshold
    curr = _make_snapshot([_make_pool("0xA", "BNB-USDT", apr=12.0)])

    result = analyse_cycle(current=curr, previous=prev)

    assert result.significant_change is True
    delta = result.get_delta("0xA")
    assert abs(delta.apr_change_abs - 2.0) < 1e-9
    assert abs(delta.apr_change_pct - 0.2) < 1e-9   # 20% relative increase
    assert not delta.is_anomalous

    print("[PASS] analyse_cycle() — significant change detected")


# ---------------------------------------------------------------------------
# Test 4: APR spike anomaly
# ---------------------------------------------------------------------------

def test_apr_spike_anomaly():
    from core.analyser import analyse_cycle, detect_anomalies, PoolDelta

    # APR doubles from 10% to 15.5% → > 50% relative increase (spike threshold)
    prev = _make_snapshot([_make_pool("0xA", "USDT-USDC", apr=10.0)])
    curr = _make_snapshot([_make_pool("0xA", "USDT-USDC", apr=15.5)])  # +55% relative

    result = analyse_cycle(current=curr, previous=prev)

    assert len(result.anomalies) > 0
    assert "0xA" in result.anomalous_addresses
    delta = result.get_delta("0xA")
    assert delta.is_anomalous
    assert any("APR spike" in d for d in delta.anomaly_descriptions)

    # Significant change should NOT be set for anomalous pools
    assert result.significant_change is False

    # Direct detect_anomalies test with a hand-crafted delta
    from dataclasses import replace
    d = PoolDelta(
        pool_address="0xX", symbol="TEST",
        apr_change_abs=6.0, apr_change_pct=0.60,  # 60% > 50% threshold
        tvl_change_abs=0, tvl_change_pct=0.0,
        volume_change_pct=0.0,
    )
    flags = detect_anomalies(d)
    assert len(flags) == 1
    assert "APR spike" in flags[0]

    print("[PASS] APR spike anomaly detection")


# ---------------------------------------------------------------------------
# Test 5: TVL drop anomaly
# ---------------------------------------------------------------------------

def test_tvl_drop_anomaly():
    from core.analyser import analyse_cycle, detect_anomalies, PoolDelta

    # TVL drops 40% → exceeds 30% threshold
    prev = _make_snapshot([_make_pool("0xB", "ETH-USDC", apr=8.0, tvl=1_000_000)])
    curr = _make_snapshot([_make_pool("0xB", "ETH-USDC", apr=8.0, tvl=600_000)])

    result = analyse_cycle(current=curr, previous=prev)

    assert len(result.anomalies) > 0
    assert "0xB" in result.anomalous_addresses
    delta = result.get_delta("0xB")
    assert delta.is_anomalous
    assert any("TVL drop" in d for d in delta.anomaly_descriptions)

    # Direct test
    d = PoolDelta(
        pool_address="0xY", symbol="TEST",
        apr_change_abs=0.0, apr_change_pct=0.0,
        tvl_change_abs=-400_000, tvl_change_pct=-0.40,  # -40% < -30% threshold
        volume_change_pct=0.0,
    )
    flags = detect_anomalies(d)
    assert len(flags) == 1
    assert "TVL drop" in flags[0]

    print("[PASS] TVL drop anomaly detection")


# ---------------------------------------------------------------------------
# Test 6: New and dropped pools
# ---------------------------------------------------------------------------

def test_new_and_dropped_pools():
    from core.analyser import analyse_cycle

    prev = _make_snapshot([
        _make_pool("0xA", "USDT-USDC", apr=5.0),
        _make_pool("0xOLD", "OLD-TOKEN", apr=3.0),   # will be dropped
    ])
    curr = _make_snapshot([
        _make_pool("0xA", "USDT-USDC", apr=5.0),
        _make_pool("0xNEW", "NEW-TOKEN", apr=20.0),  # brand new
    ])

    result = analyse_cycle(current=curr, previous=prev)

    assert result.pools_compared == 1    # only 0xA is in both
    assert result.pools_new == 1         # 0xNEW is new
    assert result.pools_dropped == 1     # 0xOLD was dropped

    print("[PASS] analyse_cycle() — new and dropped pools counted correctly")


# ---------------------------------------------------------------------------
# Test 7: clean_pools() filters anomalous entries
# ---------------------------------------------------------------------------

def test_clean_pools():
    from core.analyser import analyse_cycle
    from core.market_data import PoolData

    # Pool A is clean, Pool B will have a TVL anomaly
    prev = _make_snapshot([
        _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=1_000_000),
        _make_pool("0xB", "BNB-USDT",  apr=10.0, tvl=2_000_000),
    ])
    curr = _make_snapshot([
        _make_pool("0xA", "USDT-USDC", apr=5.1, tvl=1_001_000),
        _make_pool("0xB", "BNB-USDT",  apr=10.0, tvl=1_000_000),  # -50% TVL
    ])

    result = analyse_cycle(current=curr, previous=prev)

    # 0xB should be flagged
    assert "0xB" in result.anomalous_addresses

    clean = result.clean_pools(curr.pools)
    assert len(clean) == 1
    assert clean[0].pool == "0xA"

    print("[PASS] AnalysisResult.clean_pools() filters anomalous pools")


# ---------------------------------------------------------------------------
# Test 8: stability score
# ---------------------------------------------------------------------------

def test_stability_score():
    from core.analyser import get_pool_stability_score, PoolDelta

    # No history → neutral 0.5
    score = get_pool_stability_score("0xA", [])
    assert score == 0.5, f"Expected 0.5, got {score}"

    # Very stable: tiny APR changes
    stable_history = [
        PoolDelta("0xA", "T", apr_change_abs=0.1, apr_change_pct=0.01,
                  tvl_change_abs=0, tvl_change_pct=0.0, volume_change_pct=0.0)
        for _ in range(5)
    ]
    score_stable = get_pool_stability_score("0xA", stable_history)
    assert score_stable > 0.9, f"Expected > 0.9, got {score_stable}"

    # Volatile: large APR changes (5pp average)
    volatile_history = [
        PoolDelta("0xA", "T", apr_change_abs=5.0, apr_change_pct=0.5,
                  tvl_change_abs=0, tvl_change_pct=0.0, volume_change_pct=0.0)
        for _ in range(5)
    ]
    score_volatile = get_pool_stability_score("0xA", volatile_history)
    assert score_volatile < 0.6, f"Expected < 0.6, got {score_volatile}"

    # Score clamps to 0 when mean change >= normaliser (10pp)
    extreme_history = [
        PoolDelta("0xA", "T", apr_change_abs=15.0, apr_change_pct=1.5,
                  tvl_change_abs=0, tvl_change_pct=0.0, volume_change_pct=0.0)
        for _ in range(3)
    ]
    score_extreme = get_pool_stability_score("0xA", extreme_history)
    assert score_extreme == 0.0, f"Expected 0.0, got {score_extreme}"

    # Address filter: history for 0xB should not affect score for 0xA
    mixed_history = stable_history + [
        PoolDelta("0xB", "OTHER", apr_change_abs=9.0, apr_change_pct=0.9,
                  tvl_change_abs=0, tvl_change_pct=0.0, volume_change_pct=0.0)
    ]
    score_filtered = get_pool_stability_score("0xA", mixed_history)
    assert abs(score_filtered - score_stable) < 1e-9, \
        "Score should not be affected by other pools' history"

    print("[PASS] get_pool_stability_score()")


# ---------------------------------------------------------------------------
# Test 9: price deviation check
# ---------------------------------------------------------------------------

def test_price_deviation():
    from core.analyser import check_price_deviation

    # No on-chain data → None
    prev = _make_pool("0xA", "BNB-USDT", apr=10.0)
    curr = _make_pool("0xA", "BNB-USDT", apr=10.0)
    assert check_price_deviation(curr, prev) is None

    # Large price move (sqrtPrice * 1.2 → price * 1.44 → 44% change > 10% threshold)
    base_sqrt = 2**96   # represents price = 1.0
    prev_on = _make_pool("0xA", "BNB-USDT", apr=10.0, sqrt_price=base_sqrt)
    curr_on = _make_pool("0xA", "BNB-USDT", apr=10.0, sqrt_price=int(base_sqrt * 1.2))
    result = check_price_deviation(curr_on, prev_on)
    assert result is not None
    assert "Price deviation" in result

    # Small price move (1% price change < 10% threshold) → no anomaly
    curr_small = _make_pool("0xA", "BNB-USDT", apr=10.0,
                             sqrt_price=int(base_sqrt * 1.005))  # ~1% price change
    result_small = check_price_deviation(curr_small, prev_on)
    assert result_small is None

    print("[PASS] check_price_deviation()")


# ---------------------------------------------------------------------------
# Test 10: full cycle with live DeFiLlama data (two snapshots)
# ---------------------------------------------------------------------------

def test_live_two_cycle():
    """
    Fetch one live snapshot, then simulate a second cycle with tiny tweaks.
    Verifies the analyser handles real PoolData objects without errors.
    """
    from core.market_data import get_market_snapshot, invalidate_cache
    from core.analyser import analyse_cycle
    import copy

    invalidate_cache()
    snap1 = get_market_snapshot()

    if not snap1.pools:
        print("[SKIP] test_live_two_cycle() — no pools from API")
        return

    # Simulate a second snapshot with tiny APR nudge on all pools
    import copy, time
    snap2_pools = []
    for p in snap1.pools:
        p2 = copy.copy(p)
        p2.apr = p.apr + 0.05       # tiny nudge — below significance threshold
        p2.timestamp = time.time()
        snap2_pools.append(p2)

    from core.market_data import MarketSnapshot
    snap2 = MarketSnapshot(pools=snap2_pools, prices=snap1.prices)

    result = analyse_cycle(current=snap2, previous=snap1)

    assert result.first_run is False
    assert result.pools_compared == len(snap1.pools)
    # A 0.05pp nudge is below significance threshold on normal-APR pools.
    # Near-zero APR pools may trigger the relative-spike check — that is
    # correct behaviour (a +0.05pp nudge on a 0.001% APR pool IS a massive
    # relative spike). The important thing is the analyser ran without errors.
    # We only assert the structural invariants, not the anomaly count.
    assert isinstance(result.anomalies, list)
    assert isinstance(result.anomalous_addresses, set)
    # Significant change should be False: nudge is 0.05pp < 0.5pp threshold
    # for any pool that isn't anomalous.
    assert result.significant_change is False

    print(f"[PASS] test_live_two_cycle() — {result.pools_compared} pools compared, "
          f"{len(result.anomalies)} anomalies (some expected on near-zero APR pools), "
          f"significant={result.significant_change}")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_first_run()
    test_stable_cycle()
    test_significant_change()
    test_apr_spike_anomaly()
    test_tvl_drop_anomaly()
    test_new_and_dropped_pools()
    test_clean_pools()
    test_stability_score()
    test_price_deviation()
    test_live_two_cycle()
    print()
    print("All Sprint 5 tests passed.")
