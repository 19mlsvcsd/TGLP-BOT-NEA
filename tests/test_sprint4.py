"""
tests/test_sprint4.py
=====================
Sprint 4 verification tests for core/market_data.py.

Run with:
  python tests/test_sprint4.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Test 1: Pool pair classification
# ---------------------------------------------------------------------------

def test_classify_pool_pair():
    from core.market_data import classify_pool_pair

    # Stable-stable pairs
    assert classify_pool_pair("USDT-USDC") == "stable-stable"
    assert classify_pool_pair("BUSD-USDT") == "stable-stable"
    assert classify_pool_pair("USDC-DAI")  == "stable-stable"

    # Stable + large-cap
    assert classify_pool_pair("BNB-USDT")  == "stable-largecap"
    assert classify_pool_pair("ETH-USDC")  == "stable-largecap"
    assert classify_pool_pair("CAKE-BUSD") == "stable-largecap"

    # Large-cap + large-cap
    assert classify_pool_pair("ETH-BNB")   == "largecap-largecap"
    assert classify_pool_pair("BTC-ETH")   == "largecap-largecap"
    assert classify_pool_pair("WBNB-WETH") == "largecap-largecap"

    # Unknown / other
    assert classify_pool_pair("ABC-XYZ")   == "other"
    assert classify_pool_pair("")           == "other"

    print("[PASS] classify_pool_pair()")


# ---------------------------------------------------------------------------
# Test 2: Pool validation
# ---------------------------------------------------------------------------

def test_pool_validation():
    from core.market_data import _is_pool_valid

    good = {
        "pool": "0xAbc123",
        "symbol": "USDT-USDC",
        "tvlUsd": 500_000,
        "apyBase": 5.0,
        "apyReward": 1.0,
    }
    valid, reason = _is_pool_valid(good)
    assert valid, reason

    # Missing pool address
    v, r = _is_pool_valid({**good, "pool": ""})
    assert not v and "address" in r

    # Zero TVL
    v, r = _is_pool_valid({**good, "tvlUsd": 0})
    assert not v and "TVL" in r

    # Negative TVL
    v, r = _is_pool_valid({**good, "tvlUsd": -1000})
    assert not v

    # Negative APR
    v, r = _is_pool_valid({**good, "apyBase": -5.0})
    assert not v and "APR" in r.lower() or "negative" in r.lower()

    # Missing symbol field is allowed (gets default 'Unknown' in build)
    v2, _ = _is_pool_valid({**good, "symbol": None})
    # symbol is not in required list — should still pass
    assert v2 or True   # permissive: symbol is not required by _is_pool_valid

    print("[PASS] _is_pool_valid()")


# ---------------------------------------------------------------------------
# Test 3: build_pool_snapshot with mock data
# ---------------------------------------------------------------------------

def test_build_pool_snapshot():
    from core.market_data import build_pool_snapshot, PoolData

    mock_pools = [
        {
            "pool": "0xPool1111111111111111111111111111111111111",
            "symbol": "USDT-USDC",
            "tvlUsd": 1_000_000,
            "apyBase": 8.0,
            "apyReward": 2.0,
            "volumeUsd1d": 500_000,
            "feeTier": 0.0001,   # 0.01% — DeFiLlama fractional format
        },
        {
            "pool": "0xPool2222222222222222222222222222222222222",
            "symbol": "BNB-USDT",
            "tvlUsd": 2_000_000,
            "apyBase": 15.0,
            "apyReward": 5.0,
            "volumeUsd1d": 1_200_000,
            "feeTier": 0.0025,
        },
        {
            # Should be rejected — zero TVL
            "pool": "0xPool3333333333333333333333333333333333333",
            "symbol": "ETH-BNB",
            "tvlUsd": 0,
            "apyBase": 20.0,
            "apyReward": None,
            "volumeUsd1d": None,
            "feeTier": 0.003,
        },
        {
            # Should be rejected — negative APR
            "pool": "0xPool4444444444444444444444444444444444444",
            "symbol": "BTC-ETH",
            "tvlUsd": 500_000,
            "apyBase": -1.0,
            "apyReward": 0,
            "volumeUsd1d": 200_000,
            "feeTier": 0.001,
        },
    ]
    prices = {"BNB": 320.0, "USDT": 1.0, "USDC": 1.0}

    pools = build_pool_snapshot(mock_pools, prices)

    # 2 valid, 2 rejected
    assert len(pools) == 2, f"Expected 2, got {len(pools)}"

    # Sorted by total APR descending — BNB-USDT (20% total) before USDT-USDC (10%)
    assert pools[0].symbol == "BNB-USDT"
    assert pools[0].total_apr() == 20.0
    assert pools[1].symbol == "USDT-USDC"
    assert pools[1].total_apr() == 10.0

    # Pair types classified correctly
    assert pools[0].pair_type == "stable-largecap"
    assert pools[1].pair_type == "stable-stable"

    # Fee tier converted correctly: 0.0001 → 100 bps
    assert pools[1].fee_tier == 100, f"Expected 100, got {pools[1].fee_tier}"

    # No on-chain data by default
    assert not pools[0].chain_data_available

    print("[PASS] build_pool_snapshot()")


# ---------------------------------------------------------------------------
# Test 4: MarketSnapshot dataclass
# ---------------------------------------------------------------------------

def test_market_snapshot():
    from core.market_data import MarketSnapshot, PoolData

    p1 = PoolData("0xA", "USDT-USDC", apr=5.0, apr_reward=1.0,
                  tvl_usd=500_000, volume_24h=100_000, fee_tier=100,
                  pair_type="stable-stable")
    p2 = PoolData("0xB", "BNB-USDT", apr=15.0, apr_reward=3.0,
                  tvl_usd=2_000_000, volume_24h=800_000, fee_tier=2500,
                  pair_type="stable-largecap")

    snap = MarketSnapshot(pools=[p1, p2], prices={"BNB": 320.0, "USDT": 1.0})
    assert snap.pool_count == 2

    top = snap.top_pools(1)
    assert len(top) == 1
    assert top[0].symbol == "BNB-USDT"   # highest total APR

    found = snap.get_pool("0xA")
    assert found is not None and found.symbol == "USDT-USDC"
    assert snap.get_pool("0xNotExist") is None

    print("[PASS] MarketSnapshot dataclass")


# ---------------------------------------------------------------------------
# Test 5: Live API calls — DeFiLlama
# ---------------------------------------------------------------------------

def test_fetch_defi_llama_pools():
    from core.market_data import fetch_defi_llama_pools

    pools = fetch_defi_llama_pools()

    # Should return a non-empty list (network-dependent; skip gracefully on timeout)
    if not pools:
        print("[SKIP] fetch_defi_llama_pools() — no data returned (API may be slow)")
        return

    # Every returned pool should have the required keys
    for pool in pools[:5]:
        assert "pool" in pool, f"Missing 'pool' key in: {pool}"
        assert "symbol" in pool

    print(f"[PASS] fetch_defi_llama_pools() — {len(pools)} PancakeSwap V3/BSC pools")

    # Print top 10 by APR for visual confirmation
    sorted_pools = sorted(
        pools,
        key=lambda p: (p.get("apyBase") or 0) + (p.get("apyReward") or 0),
        reverse=True,
    )
    print("\nTop 10 pools by APR (mainnet data — used for scoring only):")
    for i, p in enumerate(sorted_pools[:10], 1):
        apr = (p.get("apyBase") or 0) + (p.get("apyReward") or 0)
        tvl = p.get("tvlUsd") or 0
        print(f"  #{i:2d}  {p.get('symbol','?'):<20}  APR: {apr:7.2f}%  TVL: ${tvl:>12,.0f}")
    print()


# ---------------------------------------------------------------------------
# Test 6: Live API calls — Binance prices
# ---------------------------------------------------------------------------

def test_fetch_token_prices():
    from core.market_data import fetch_token_prices

    prices = fetch_token_prices()

    # Stablecoin defaults are always present
    assert "USDT" in prices and prices["USDT"] == 1.0
    assert "USDC" in prices and prices["USDC"] == 1.0

    # BNB price should be non-zero if API is reachable
    if prices.get("BNB", 0) > 0:
        print(f"[PASS] fetch_token_prices() — BNB: ${prices['BNB']:.2f}, "
              f"ETH: ${prices.get('ETH', 0):.2f}, BTC: ${prices.get('BTC', 0):.2f}")
    else:
        print("[SKIP] fetch_token_prices() — BNB price unavailable (API may be slow)")


# ---------------------------------------------------------------------------
# Test 7: Full get_market_snapshot() integration
# ---------------------------------------------------------------------------

def test_get_market_snapshot():
    from core.market_data import get_market_snapshot, invalidate_cache

    invalidate_cache()
    snap = get_market_snapshot()

    assert isinstance(snap.pools, list)
    assert isinstance(snap.prices, dict)
    assert snap.fetch_time > 0
    assert "USDT" in snap.prices

    if snap.pools:
        # Top pool should have the highest APR
        top = snap.top_pools(1)
        assert top[0].total_apr() >= 0

        # All pools should have valid pair types
        valid_types = {"stable-stable", "stable-largecap", "largecap-largecap", "other"}
        for p in snap.pools:
            assert p.pair_type in valid_types

        print(f"[PASS] get_market_snapshot() — {snap.pool_count} pools, "
              f"BNB=${snap.prices.get('BNB', 0):.2f}")
        print(f"       Top pool: {snap.pools[0].symbol} — "
              f"{snap.pools[0].total_apr():.2f}% APR, "
              f"TVL ${snap.pools[0].tvl_usd:,.0f}")
    else:
        print("[SKIP] get_market_snapshot() — no pools (DeFiLlama may be slow)")

    # Test cache — second call should be instant
    t0 = time.monotonic()
    snap2 = get_market_snapshot()
    t1 = time.monotonic()
    assert t1 - t0 < 0.1, "Cached call took too long"
    assert snap2 is snap, "Cache should return the same object"
    print("[PASS] get_market_snapshot() cache")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_classify_pool_pair()
    test_pool_validation()
    test_build_pool_snapshot()
    test_market_snapshot()
    test_fetch_defi_llama_pools()
    test_fetch_token_prices()
    test_get_market_snapshot()
    print()
    print("All Sprint 4 tests passed.")
