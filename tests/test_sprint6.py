"""
tests/test_sprint6.py
=====================
Sprint 6 verification tests for core/decision_engine.py.

Run with:
  python tests/test_sprint6.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers: build mock pools, snapshots, strategies
# ---------------------------------------------------------------------------

def _make_pool(address, symbol, apr, tvl=500_000, volume=100_000,
               pair_type="stable-stable"):
    from core.market_data import PoolData
    return PoolData(
        pool=address, symbol=symbol,
        apr=apr, apr_reward=0.0,
        tvl_usd=tvl, volume_24h=volume,
        fee_tier=100, pair_type=pair_type,
    )


def _make_strategy(name="Test", pair_types=None, min_tvl=100_000,
                   rebalance_threshold=0.15, auto_execute=False):
    from config.settings import StrategyConfig
    return StrategyConfig(
        name=name,
        description="Test strategy",
        allowed_pair_types=pair_types or ["stable-stable", "stable-largecap"],
        min_tvl_usd=min_tvl,
        max_slippage=0.5,
        rebalance_threshold=rebalance_threshold,
        compound_interval=3600,
        auto_execute=auto_execute,
    )


def _make_analysis_result(anomalous_addresses=None):
    from core.analyser import AnalysisResult
    return AnalysisResult(
        pool_deltas=[],
        anomalies=[],
        anomalous_addresses=set(anomalous_addresses or []),
        significant_change=False,
        first_run=False,
        pools_compared=0,
    )


# ---------------------------------------------------------------------------
# Test 1: filter_pools_by_strategy: pair type filtering
# ---------------------------------------------------------------------------

def test_filter_pair_type():
    from core.decision_engine import filter_pools_by_strategy

    pools = [
        _make_pool("0xA", "USDT-USDC",  apr=5.0,  pair_type="stable-stable"),
        _make_pool("0xB", "BNB-USDT",   apr=10.0, pair_type="stable-largecap"),
        _make_pool("0xC", "BNB-ETH",    apr=20.0, pair_type="largecap-largecap"),
        _make_pool("0xD", "SHIB-FLOKI", apr=50.0, pair_type="other"),
    ]
    strategy = _make_strategy(pair_types=["stable-stable", "stable-largecap"])

    result = filter_pools_by_strategy(pools, strategy)

    assert len(result) == 2
    addresses = {p.pool for p in result}
    assert "0xA" in addresses
    assert "0xB" in addresses
    assert "0xC" not in addresses
    assert "0xD" not in addresses

    print("[PASS] filter_pools_by_strategy(): pair type filtering")


# ---------------------------------------------------------------------------
# Test 2: filter_pools_by_strategy: TVL floor
# ---------------------------------------------------------------------------

def test_filter_tvl_floor():
    from core.decision_engine import filter_pools_by_strategy

    pools = [
        _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=1_000_000, pair_type="stable-stable"),
        _make_pool("0xB", "USDT-DAI",  apr=6.0, tvl=50_000,    pair_type="stable-stable"),  # below floor
        _make_pool("0xC", "USDT-BUSD", apr=4.0, tvl=200_000,   pair_type="stable-stable"),
    ]
    strategy = _make_strategy(pair_types=["stable-stable"], min_tvl=100_000)

    result = filter_pools_by_strategy(pools, strategy)

    assert len(result) == 2
    addresses = {p.pool for p in result}
    assert "0xA" in addresses
    assert "0xB" not in addresses
    assert "0xC" in addresses

    print("[PASS] filter_pools_by_strategy(): TVL floor")


# ---------------------------------------------------------------------------
# Test 3: filter_pools_by_strategy: anomaly exclusion
# ---------------------------------------------------------------------------

def test_filter_anomalies():
    from core.decision_engine import filter_pools_by_strategy

    pools = [
        _make_pool("0xA", "USDT-USDC", apr=5.0, pair_type="stable-stable"),
        _make_pool("0xB", "USDT-DAI",  apr=6.0, pair_type="stable-stable"),  # anomalous
    ]
    strategy = _make_strategy(pair_types=["stable-stable"], min_tvl=0)
    analysis = _make_analysis_result(anomalous_addresses=["0xB"])

    result = filter_pools_by_strategy(pools, strategy, analysis)

    assert len(result) == 1
    assert result[0].pool == "0xA"

    print("[PASS] filter_pools_by_strategy(): anomaly exclusion")


# ---------------------------------------------------------------------------
# Test 4: filter_pools_by_strategy: no analysis_result (first run)
# ---------------------------------------------------------------------------

def test_filter_no_analysis():
    """When analysis_result is None, no anomaly filtering is applied."""
    from core.decision_engine import filter_pools_by_strategy

    pools = [
        _make_pool("0xA", "USDT-USDC", apr=5.0, pair_type="stable-stable"),
        _make_pool("0xB", "USDT-DAI",  apr=6.0, pair_type="stable-stable"),
    ]
    strategy = _make_strategy(pair_types=["stable-stable"], min_tvl=0)

    result = filter_pools_by_strategy(pools, strategy, analysis_result=None)

    assert len(result) == 2
    print("[PASS] filter_pools_by_strategy(): no analysis_result (first run)")


# ---------------------------------------------------------------------------
# Test 5: score_pools: single pool receives 0.5 for all normalised components
# ---------------------------------------------------------------------------

def test_score_single_pool():
    from core.decision_engine import score_pools

    pool = _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=500_000, volume=100_000)
    result = score_pools([pool])

    assert len(result) == 1
    sp = result[0]
    # All components should be 0.5 (single-pool normalisation)
    assert sp.norm_apr == 0.5
    assert sp.norm_tvl == 0.5
    assert sp.norm_volume == 0.5
    assert sp.norm_stability == 0.5
    # Score = 0.40*0.5 + 0.30*0.5 + 0.20*0.5 + 0.10*0.5 = 0.5
    assert abs(sp.score - 0.5) < 1e-9

    print("[PASS] score_pools(): single pool, all normalised components = 0.5")


# ---------------------------------------------------------------------------
# Test 6: score_pools: ranking order: high APR, high TVL, high volume
# ---------------------------------------------------------------------------

def test_score_ranking():
    from core.decision_engine import score_pools

    # Pool A: high APR, moderate TVL
    # Pool B: low APR, high TVL
    # Pool C: balanced
    pool_a = _make_pool("0xA", "HIGH-APR",  apr=20.0, tvl=300_000, volume=50_000)
    pool_b = _make_pool("0xB", "HIGH-TVL",  apr=5.0,  tvl=900_000, volume=200_000)
    pool_c = _make_pool("0xC", "BALANCED",  apr=12.0, tvl=600_000, volume=100_000)

    scored = score_pools([pool_a, pool_b, pool_c])

    assert len(scored) == 3
    # All scores are in [0, 1]
    for sp in scored:
        assert 0.0 <= sp.score <= 1.0

    # The top scorer should have norm_apr=1.0 (pool_a has highest APR)
    top = scored[0]
    # The bottom pool has norm_apr=0.0 (pool_b has lowest APR)
    bottom = scored[-1]

    # Find pool_a in scored list
    sp_a = next(sp for sp in scored if sp.pool.pool == "0xA")
    sp_b = next(sp for sp in scored if sp.pool.pool == "0xB")
    assert sp_a.norm_apr == 1.0
    assert sp_b.norm_apr == 0.0

    # Scores should be sorted highest first
    for i in range(len(scored) - 1):
        assert scored[i].score >= scored[i + 1].score, "Scores not sorted descending"

    print("[PASS] score_pools(): ranking order correct, normalisation correct")


# ---------------------------------------------------------------------------
# Test 7: score_pools: empty input returns empty list
# ---------------------------------------------------------------------------

def test_score_empty():
    from core.decision_engine import score_pools

    result = score_pools([])
    assert result == []
    print("[PASS] score_pools(): empty input returns empty list")


# ---------------------------------------------------------------------------
# Test 8: score_pools: identical pools get 0.5 for tied metrics
# ---------------------------------------------------------------------------

def test_score_identical_pools():
    """When all pools have the same APR/TVL/volume, all normalised values = 0.5."""
    from core.decision_engine import score_pools

    pools = [
        _make_pool(f"0x{i}", "USDT-USDC", apr=5.0, tvl=500_000, volume=100_000)
        for i in range(3)
    ]
    scored = score_pools(pools)
    for sp in scored:
        assert sp.norm_apr == 0.5
        assert sp.norm_tvl == 0.5
        assert sp.norm_volume == 0.5
        assert abs(sp.score - 0.5) < 1e-9

    print("[PASS] score_pools(): identical pools all score 0.5")


# ---------------------------------------------------------------------------
# Test 9: make_decision: no pools --> NO_ACTION
# ---------------------------------------------------------------------------

def test_decision_no_pools():
    from core.decision_engine import make_decision, Decision

    strategy = _make_strategy()
    result = make_decision(
        scored_pools=[],
        current_position=None,
        strategy=strategy,
        analysis_result=None,
        pools_filtered_count=5,
    )

    assert result.action == Decision.NO_ACTION
    assert result.target_pool is None
    assert result.pools_considered == 0
    assert result.pools_filtered == 5
    assert result.estimated_gas_units == 0

    print("[PASS] make_decision() -- no pools: NO_ACTION")


# ---------------------------------------------------------------------------
# Test 10: make_decision: no position --> ALLOCATE
# ---------------------------------------------------------------------------

def test_decision_allocate():
    from core.decision_engine import make_decision, Decision, score_pools

    pools = [
        _make_pool("0xA", "USDT-USDC", apr=5.0,  tvl=500_000),
        _make_pool("0xB", "BNB-USDT",  apr=12.0, tvl=800_000, pair_type="stable-largecap"),
    ]
    strategy = _make_strategy(pair_types=["stable-stable", "stable-largecap"], min_tvl=0)
    scored = score_pools(pools)

    result = make_decision(
        scored_pools=scored,
        current_position=None,
        strategy=strategy,
        analysis_result=None,
    )

    assert result.action == Decision.ALLOCATE
    assert result.target_pool is not None
    # Top-scored pool should be the target
    assert result.target_pool.pool == scored[0].pool.pool
    assert result.estimated_gas_units > 0
    assert result.estimated_gas_bnb > 0
    assert result.pools_considered == 2

    print("[PASS] make_decision(): no position --> ALLOCATE")


# ---------------------------------------------------------------------------
# Test 11: make_decision: better pool found --> REBALANCE
# ---------------------------------------------------------------------------

def test_decision_rebalance():
    from core.decision_engine import make_decision, Decision, score_pools

    # Current pool is 0xA (low APR), better pool is 0xB (high APR)
    pool_current = _make_pool("0xA", "USDT-USDC", apr=3.0, tvl=200_000)
    pool_better  = _make_pool("0xB", "BNB-USDT",  apr=25.0, tvl=1_000_000, pair_type="stable-largecap")
    strategy = _make_strategy(
        pair_types=["stable-stable", "stable-largecap"],
        min_tvl=0,
        rebalance_threshold=0.10,  # 10% score improvement needed
    )
    scored = score_pools([pool_current, pool_better])

    current_position = {"pool_address": "0xA", "token_id": 1}

    result = make_decision(
        scored_pools=scored,
        current_position=current_position,
        strategy=strategy,
        analysis_result=None,
    )

    assert result.action == Decision.REBALANCE
    assert result.target_pool is not None
    assert result.target_pool.pool == "0xB"
    assert result.current_pool is not None
    assert result.current_pool.pool == "0xA"
    assert result.estimated_gas_units > 0

    print("[PASS] make_decision(): better pool found --> REBALANCE")


# ---------------------------------------------------------------------------
# Test 12: make_decision: position stable, compound enabled --> COMPOUND
# ---------------------------------------------------------------------------

def test_decision_compound():
    from core.decision_engine import make_decision, Decision, score_pools

    # Only one pool (current pool), no rebalance opportunity
    pool = _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=500_000)
    strategy = _make_strategy(pair_types=["stable-stable"], min_tvl=0, rebalance_threshold=0.15)
    scored = score_pools([pool])

    current_position = {"pool_address": "0xA", "token_id": 1}

    result = make_decision(
        scored_pools=scored,
        current_position=current_position,
        strategy=strategy,
        analysis_result=None,
        compound_enabled=True,
        fees_available=True,
    )

    assert result.action == Decision.COMPOUND
    assert result.target_pool is not None
    assert result.target_pool.pool == "0xA"
    assert result.estimated_gas_units > 0

    print("[PASS] make_decision(): compound enabled + fees available --> COMPOUND")


# ---------------------------------------------------------------------------
# Test 13: make_decision: stable position, no compound --> NO_ACTION
# ---------------------------------------------------------------------------

def test_decision_no_action_stable():
    from core.decision_engine import make_decision, Decision, score_pools

    pool = _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=500_000)
    strategy = _make_strategy(pair_types=["stable-stable"], min_tvl=0, rebalance_threshold=0.15)
    scored = score_pools([pool])

    current_position = {"pool_address": "0xA", "token_id": 1}

    result = make_decision(
        scored_pools=scored,
        current_position=current_position,
        strategy=strategy,
        analysis_result=None,
        compound_enabled=False,
        fees_available=False,
    )

    assert result.action == Decision.NO_ACTION
    assert result.target_pool is None

    print("[PASS] make_decision(): stable position, no compound --> NO_ACTION")


# ---------------------------------------------------------------------------
# Test 14: make_decision: rebalance threshold not met --> NO_ACTION (not REBALANCE)
# ---------------------------------------------------------------------------

def test_decision_threshold_not_met():
    """When score gap is below rebalance_threshold, no rebalance happens.

    With only two pools, min-max normalisation spreads them to [0,1] on every
    metric, making tiny value differences produce large score gaps. A third
    clearly-inferior pool acts as anchor so pool_a and pool_b remain close
    in normalised space.
    """
    from core.decision_engine import make_decision, Decision, score_pools

    # pool_a (current): good but not top
    # pool_b (slightly better): top scored pool
    # pool_c (anchor): clearly worst, forces normalisation range to be wide
    pool_a = _make_pool("0xA", "USDT-USDC", apr=10.0, tvl=500_000, volume=100_000)
    pool_b = _make_pool("0xB", "USDT-DAI",  apr=11.0, tvl=520_000, volume=100_000)
    pool_c = _make_pool("0xC", "USDT-BUSD", apr=1.0,  tvl=100_000, volume=100_000)
    strategy = _make_strategy(
        pair_types=["stable-stable"],
        min_tvl=0,
        rebalance_threshold=0.10,  # gap between pool_a and pool_b ~0.054 < 0.10
    )
    scored = score_pools([pool_a, pool_b, pool_c])

    # Verify the gap is genuinely small (below threshold)
    sp_a = next(s for s in scored if s.pool.pool == "0xA")
    sp_b = next(s for s in scored if s.pool.pool == "0xB")
    assert sp_b.score > sp_a.score  # pool_b is better
    assert (sp_b.score - sp_a.score) < strategy.rebalance_threshold, (
        f"Score gap {sp_b.score - sp_a.score:.4f} unexpectedly exceeds threshold "
        f"{strategy.rebalance_threshold}; adjust test pool values"
    )

    current_position = {"pool_address": "0xA", "token_id": 1}

    result = make_decision(
        scored_pools=scored,
        current_position=current_position,
        strategy=strategy,
        analysis_result=None,
        compound_enabled=False,
    )

    assert result.action == Decision.NO_ACTION, (
        f"Expected NO_ACTION, got {result.action}. "
        f"Score gap was {scored[0].score - next(s for s in scored if s.pool.pool == '0xA').score:.3f}"
    )

    print("[PASS] make_decision(): rebalance threshold not met --> NO_ACTION")


# ---------------------------------------------------------------------------
# Test 15: make_decision: current pool is anomalous (not in scored list)
# ---------------------------------------------------------------------------

def test_decision_current_pool_anomalous():
    """When the current pool was filtered out (anomalous), its score is 0 --> REBALANCE."""
    from core.decision_engine import make_decision, Decision, score_pools

    # Scored list only contains the new pool (current was filtered out as anomalous)
    pool_new = _make_pool("0xB", "BNB-USDT", apr=10.0, tvl=700_000, pair_type="stable-largecap")
    strategy = _make_strategy(pair_types=["stable-largecap"], min_tvl=0, rebalance_threshold=0.10)
    scored = score_pools([pool_new])

    # Current position points to 0xA which is NOT in scored list
    current_position = {"pool_address": "0xA", "token_id": 1}

    result = make_decision(
        scored_pools=scored,
        current_position=current_position,
        strategy=strategy,
        analysis_result=None,
    )

    # Current score = 0 (not found), top score = 0.5 --> gap = 0.5 > threshold 0.10
    assert result.action == Decision.REBALANCE
    assert result.target_pool.pool == "0xB"
    assert result.current_pool is None   # current pool data not available

    print("[PASS] make_decision(): current pool anomalous --> forced REBALANCE")


# ---------------------------------------------------------------------------
# Test 16: format_decision_summary: smoke test for all four actions
# ---------------------------------------------------------------------------

def test_format_decision_summary():
    from core.decision_engine import (
        format_decision_summary, Decision, DecisionResult, ScoredPool
    )

    pool = _make_pool("0xA", "USDT-USDC", apr=5.0, tvl=500_000, volume=100_000)
    sp = ScoredPool(
        pool=pool, score=0.65,
        norm_apr=0.8, norm_tvl=0.6, norm_stability=0.5, norm_volume=0.5,
    )

    for action in Decision:
        target = pool if action != Decision.NO_ACTION else None
        result = DecisionResult(
            action=action,
            target_pool=target,
            reasoning="Test reasoning for this action.",
            scored_pools=[sp],
            pools_considered=5,
            pools_filtered=2,
            estimated_gas_units=860_000 if action != Decision.NO_ACTION else 0,
            estimated_gas_bnb=0.0043 if action != Decision.NO_ACTION else 0.0,
        )
        summary = format_decision_summary(result)
        # Must be a non-empty string
        assert isinstance(summary, str) and len(summary) > 0
        # Must contain the action name
        action_label = action.value.replace("_", " ").title()
        assert action_label in summary or action_label.lower() in summary.lower(), \
            f"Expected action label '{action_label}' in summary"

    print("[PASS] format_decision_summary(): smoke test all four actions")


# ---------------------------------------------------------------------------
# Test 17: _estimate_gas: correct unit/BNB values for each action
# ---------------------------------------------------------------------------

def test_estimate_gas():
    from core.decision_engine import _estimate_gas, Decision, _GAS_PRICE_REFERENCE_GWEI
    from config.settings import (
        GAS_LIMIT_APPROVE, GAS_LIMIT_SWAP, GAS_LIMIT_ADD_LIQUIDITY,
        GAS_LIMIT_REMOVE_LIQUIDITY, GAS_LIMIT_COLLECT,
    )

    # NO_ACTION
    units, bnb = _estimate_gas(Decision.NO_ACTION)
    assert units == 0
    assert bnb == 0.0

    # ALLOCATE = approve + swap + add_liquidity
    units_alloc, bnb_alloc = _estimate_gas(Decision.ALLOCATE)
    expected_alloc = GAS_LIMIT_APPROVE + GAS_LIMIT_SWAP + GAS_LIMIT_ADD_LIQUIDITY
    assert units_alloc == expected_alloc
    expected_bnb_alloc = expected_alloc * _GAS_PRICE_REFERENCE_GWEI * 1e9 / 1e18
    assert abs(bnb_alloc - expected_bnb_alloc) < 1e-18

    # REBALANCE = remove + collect + approve + swap + add
    units_reb, _ = _estimate_gas(Decision.REBALANCE)
    expected_reb = (GAS_LIMIT_REMOVE_LIQUIDITY + GAS_LIMIT_COLLECT
                    + GAS_LIMIT_APPROVE + GAS_LIMIT_SWAP + GAS_LIMIT_ADD_LIQUIDITY)
    assert units_reb == expected_reb
    assert units_reb > units_alloc  # rebalance is always more gas than allocate

    # COMPOUND = collect + add_liquidity
    units_comp, _ = _estimate_gas(Decision.COMPOUND)
    expected_comp = GAS_LIMIT_COLLECT + GAS_LIMIT_ADD_LIQUIDITY
    assert units_comp == expected_comp

    print("[PASS] _estimate_gas(): correct units and BNB for all actions")


# ---------------------------------------------------------------------------
# Test 18: full pipeline with live DeFiLlama data
# ---------------------------------------------------------------------------

def test_live_pipeline():
    """
    Fetch a live snapshot and run the full filtering + scoring + decision pipeline.
    Verifies the pipeline runs without errors on real data.
    """
    from core.market_data import get_market_snapshot, invalidate_cache
    from core.decision_engine import (
        filter_pools_by_strategy, score_pools, make_decision, Decision
    )
    from config.settings import BALANCED_GROWTH

    invalidate_cache()
    snap = get_market_snapshot()

    if not snap.pools:
        print("[SKIP] test_live_pipeline(): no pools from API")
        return

    strategy = BALANCED_GROWTH

    filtered = filter_pools_by_strategy(snap.pools, strategy, analysis_result=None)
    assert isinstance(filtered, list)

    if not filtered:
        print(f"[SKIP] test_live_pipeline(): no pools passed BALANCED_GROWTH filter "
              f"(tried {len(snap.pools)} pools)")
        return

    scored = score_pools(filtered)
    assert len(scored) == len(filtered)

    # All scores in valid range
    for sp in scored:
        assert 0.0 <= sp.score <= 1.0, f"Score out of range: {sp.score}"

    # Scores should be sorted descending
    for i in range(len(scored) - 1):
        assert scored[i].score >= scored[i + 1].score, "Scores not sorted descending"

    # Decision with no position --> ALLOCATE
    result = make_decision(
        scored_pools=scored,
        current_position=None,
        strategy=strategy,
        analysis_result=None,
    )
    assert result.action == Decision.ALLOCATE
    assert result.target_pool is not None
    assert result.pools_considered == len(filtered)

    print(
        f"[PASS] test_live_pipeline(): "
        f"{len(snap.pools)} pools fetched, "
        f"{len(filtered)} passed BALANCED_GROWTH filter, "
        f"top pool: {scored[0].pool.symbol} (score: {scored[0].score:.3f}), "
        f"decision: {result.action.value}"
    )


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_filter_pair_type()
    test_filter_tvl_floor()
    test_filter_anomalies()
    test_filter_no_analysis()
    test_score_single_pool()
    test_score_ranking()
    test_score_empty()
    test_score_identical_pools()
    test_decision_no_pools()
    test_decision_allocate()
    test_decision_rebalance()
    test_decision_compound()
    test_decision_no_action_stable()
    test_decision_threshold_not_met()
    test_decision_current_pool_anomalous()
    test_format_decision_summary()
    test_estimate_gas()
    test_live_pipeline()
    print()
    print("All Sprint 6 tests passed.")
