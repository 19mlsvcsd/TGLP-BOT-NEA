"""
core/market_data.py
===================
Market data fetching, classification, and snapshot assembly for TGLP Bot.

This module is the single source of external data for the rest of the system.
It combines two data sources:

1. **DeFiLlama Yields API** — returns APR, TVL, and 24h volume for all pools
   across all chains and protocols. We filter for PancakeSwap V3 on BSC.

2. **Binance Public Price API** — returns current spot prices for BNB, ETH,
   BTC, and CAKE. Used to convert gas costs to USD and for P&L calculations.

3. **On-chain pool contract calls** — reads slot0 (current price/tick) and
   liquidity directly from individual pool contracts on BSC Testnet RPC.

Important caveat — testnet vs. mainnet data:
    DeFiLlama and Binance return MAINNET data. For this testnet development
    project, we use mainnet pool data for discovery and scoring, then execute
    transactions on BSC Testnet. This is a known and documented limitation.
    The on-chain calls (slot0, liquidity) hit BSC Testnet RPC directly.

The public entry point is `get_market_snapshot()`, which orchestrates all
three sources and returns a validated `MarketSnapshot` dataclass. All other
modules that need market data call this function exclusively.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from web3 import Web3

from config.settings import (
    ABI_POOL,
    API_RETRY_COUNT,
    BINANCE_PRICE_URL,
    DEFI_LLAMA_POOLS_URL,
    LARGECAP_SYMBOLS,
    PRICE_SYMBOLS,
    SNAPSHOT_CACHE_SECONDS,
    STABLECOIN_SYMBOLS,
)

logger = logging.getLogger(__name__)

# Request timeout for external API calls, in seconds.
_REQUEST_TIMEOUT: int = 10

# DeFiLlama project slugs for PancakeSwap on BSC.
#
# Verified against the live API (2026-04-14): DeFiLlama tracks BSC PancakeSwap
# pools under "pancakeswap-amm" (38 pools). The "pancakeswap-amm-v3" slug exists
# but covers only Base and Ethereum — no BSC V3 pools are listed under it.
# This is a known DeFiLlama data gap for PancakeSwap V3 on BSC mainnet.
#
# For this project we accept all PancakeSwap AMM pools on BSC for discovery
# and scoring. Execution still uses PancakeSwap V3 contracts on BSC Testnet.
_DEFILLAMA_PROJECTS: tuple = ("pancakeswap-amm", "pancakeswap-amm-v3")

# DeFiLlama chain name for BSC.
_DEFILLAMA_CHAIN: str = "BSC"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PoolData:
    """
    Normalised, validated representation of one liquidity pool.

    All monetary values are in USD. APR is expressed as a percentage
    (e.g., 12.5 means 12.5% APR). Fields sourced from DeFiLlama are
    marked with (API); fields from on-chain calls are marked (chain).

    Attributes:
        pool:         Pool contract address string (from DeFiLlama).
        symbol:       Human-readable pair name, e.g. "USDT-USDC".    (API)
        apr:          Base APR as a percentage.                        (API)
        apr_reward:   Additional reward APR (farming incentives).      (API)
        tvl_usd:      Total Value Locked in USD.                       (API)
        volume_24h:   24-hour trading volume in USD.                   (API)
        fee_tier:     Fee tier in basis points, e.g. 100 = 0.01%.      (API/chain)
        pair_type:    Classification: 'stable-stable', 'stable-largecap',
                      'largecap-largecap', or 'other'.                 (derived)
        sqrt_price_x96: Current price as sqrtPriceX96 integer.        (chain)
        current_tick: Current tick index.                              (chain)
        on_chain_liquidity: Raw liquidity integer from the contract.   (chain)
        chain_data_available: True if on-chain call succeeded.
        timestamp:    Unix timestamp when this data was fetched.
    """
    pool: str
    symbol: str
    apr: float
    apr_reward: float
    tvl_usd: float
    volume_24h: float
    fee_tier: int
    pair_type: str

    # On-chain data — populated by fetch_on_chain_pool_data()
    sqrt_price_x96: Optional[int] = None
    current_tick: Optional[int] = None
    on_chain_liquidity: Optional[int] = None
    chain_data_available: bool = False

    timestamp: float = field(default_factory=time.time)

    def total_apr(self) -> float:
        """Return base APR plus any reward APR."""
        return self.apr + self.apr_reward


@dataclass
class MarketSnapshot:
    """
    A complete, validated picture of the market at one point in time.

    Produced by `get_market_snapshot()` and stored in UserSession.previous_snapshot
    so the analyser can compute per-cycle deltas.

    Attributes:
        pools:         List of validated PoolData objects, sorted by APR descending.
        prices:        Dict of symbol → price in USD, e.g. {"BNB": 320.50}.
        fetch_time:    Unix timestamp when the snapshot was assembled.
        pool_count:    Number of pools in this snapshot (convenience field).
        api_warnings:  List of non-fatal issues encountered during fetch
                       (e.g., "Binance price fetch failed — using cached values").
    """
    pools: List[PoolData]
    prices: Dict[str, float]
    fetch_time: float = field(default_factory=time.time)
    pool_count: int = 0
    api_warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.pool_count = len(self.pools)

    def get_pool(self, address: str) -> Optional[PoolData]:
        """Look up a pool by address. Returns None if not found."""
        addr_lower = address.lower()
        for pool in self.pools:
            if pool.pool.lower() == addr_lower:
                return pool
        return None

    def top_pools(self, n: int = 10) -> List[PoolData]:
        """Return the top N pools sorted by total APR descending."""
        return sorted(self.pools, key=lambda p: p.total_apr(), reverse=True)[:n]


# ---------------------------------------------------------------------------
# Module-level snapshot cache
# ---------------------------------------------------------------------------
# Caching avoids hammering the DeFiLlama API on every 15-second cycle.
# The cache is invalidated after SNAPSHOT_CACHE_SECONDS (30s by default).

_cached_snapshot: Optional[MarketSnapshot] = None
_cache_timestamp: float = 0.0


# ---------------------------------------------------------------------------
# ABI loader (reused from blockchain.py pattern)
# ---------------------------------------------------------------------------

def _load_pool_abi() -> list:
    """Load the PancakeSwap V3 pool ABI from disk."""
    project_root = Path(__file__).parent.parent
    with open(project_root / ABI_POOL, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# DeFiLlama pool fetching
# ---------------------------------------------------------------------------

def fetch_defi_llama_pools() -> List[Dict[str, Any]]:
    """
    Fetch pool data from the DeFiLlama yields API and filter for
    PancakeSwap V3 pools on BSC.

    The API returns a large JSON payload (~10 MB) with pools from every
    protocol and chain. We filter client-side to keep only the pools
    matching our project slug and chain.

    Returns:
        List of raw pool dicts from DeFiLlama, each containing at minimum:
        'pool' (address), 'symbol', 'apy', 'apyBase', 'apyReward', 'tvlUsd',
        'volumeUsd1d', 'feeTier'.

        Returns an empty list if the API call fails after retries.
    """
    for attempt in range(API_RETRY_COUNT + 1):
        try:
            response = requests.get(
                DEFI_LLAMA_POOLS_URL,
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            all_pools = response.json().get("data", [])

            # Filter for PancakeSwap AMM pools on BSC.
            # Both "pancakeswap-amm" and "pancakeswap-amm-v3" are accepted —
            # see _DEFILLAMA_PROJECTS comment for explanation of the data gap.
            filtered = [
                p for p in all_pools
                if (
                    p.get("project", "").lower() in _DEFILLAMA_PROJECTS
                    and p.get("chain", "").upper() == _DEFILLAMA_CHAIN
                )
            ]
            logger.info(
                "DeFiLlama: fetched %d total pools, %d match PancakeSwap/BSC",
                len(all_pools), len(filtered),
            )
            return filtered

        except requests.exceptions.Timeout:
            logger.warning("DeFiLlama API timed out (attempt %d)", attempt + 1)
        except requests.exceptions.HTTPError as e:
            logger.warning("DeFiLlama HTTP error: %s (attempt %d)", e, attempt + 1)
        except Exception as e:
            logger.warning("DeFiLlama fetch failed: %s (attempt %d)", e, attempt + 1)

        if attempt < API_RETRY_COUNT:
            time.sleep(1)  # Brief pause before retry.

    logger.error("DeFiLlama fetch failed after %d attempts — returning empty list", API_RETRY_COUNT + 1)
    return []


# ---------------------------------------------------------------------------
# Binance price fetching
# ---------------------------------------------------------------------------

def fetch_token_prices() -> Dict[str, float]:
    """
    Fetch current spot prices for key tokens from the Binance public API.

    Prices are returned as USD values keyed by the base token symbol
    (e.g., {"BNB": 320.50, "ETH": 3400.00}).

    Returns:
        Dict of symbol → price in USD.
        Returns cached prices or best-effort partial dict on failure.
    """
    prices: Dict[str, float] = {}

    for attempt in range(API_RETRY_COUNT + 1):
        try:
            response = requests.get(
                BINANCE_PRICE_URL,
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            # Build a lookup dict from the list of {"symbol": ..., "price": ...} items.
            price_lookup = {item["symbol"]: float(item["price"]) for item in data}

            for pair_symbol in PRICE_SYMBOLS:
                if pair_symbol in price_lookup:
                    # Strip the "USDT" suffix to get the base token name.
                    base = pair_symbol.replace("USDT", "")
                    prices[base] = price_lookup[pair_symbol]

            # Add USDT/USDC/BUSD as stable $1 pegs (not listed as pairs on Binance).
            prices.setdefault("USDT", 1.0)
            prices.setdefault("USDC", 1.0)
            prices.setdefault("BUSD", 1.0)

            logger.info("Binance prices fetched: %s", {k: f"${v:.2f}" for k, v in prices.items()})
            return prices

        except requests.exceptions.Timeout:
            logger.warning("Binance API timed out (attempt %d)", attempt + 1)
        except requests.exceptions.HTTPError as e:
            logger.warning("Binance HTTP error: %s (attempt %d)", e, attempt + 1)
        except Exception as e:
            logger.warning("Binance price fetch failed: %s (attempt %d)", e, attempt + 1)

        if attempt < API_RETRY_COUNT:
            time.sleep(1)

    logger.error("Binance price fetch failed after %d attempts — using stablecoin defaults", API_RETRY_COUNT + 1)
    # Return stablecoin prices as a safe minimum fallback so gas estimates
    # still work even if Binance is unreachable.
    return {"USDT": 1.0, "USDC": 1.0, "BUSD": 1.0, "BNB": 0.0}


# ---------------------------------------------------------------------------
# On-chain pool data
# ---------------------------------------------------------------------------

def fetch_on_chain_pool_data(
    w3: Web3, pool_address: str
) -> Dict[str, Any]:
    """
    Read real-time pool state directly from the PancakeSwap V3 pool contract.

    This supplements DeFiLlama's API data (which can lag by several minutes)
    with live on-chain values for the current price and liquidity.

    Reads three view functions:
    - slot0(): returns sqrtPriceX96, current tick, and other state.
    - liquidity(): returns the current in-range liquidity as uint128.
    - fee(): returns the pool's fee tier as uint24 (100, 500, 2500, or 10000).

    Args:
        w3:           Connected Web3 instance (BSC Testnet).
        pool_address: Checksummed pool contract address.

    Returns:
        Dict with keys: 'sqrt_price_x96' (int), 'tick' (int),
        'liquidity' (int), 'fee' (int), 'success' (bool).
        On failure, 'success' is False and numeric fields are 0.
    """
    result: Dict[str, Any] = {
        "sqrt_price_x96": 0,
        "tick": 0,
        "liquidity": 0,
        "fee": 0,
        "success": False,
    }
    try:
        abi = _load_pool_abi()
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address), abi=abi
        )

        slot0 = pool.functions.slot0().call()
        result["sqrt_price_x96"] = slot0[0]
        result["tick"] = slot0[1]

        result["liquidity"] = pool.functions.liquidity().call()
        result["fee"] = pool.functions.fee().call()
        result["success"] = True

        logger.debug(
            "On-chain pool %s: tick=%d, liquidity=%d, fee=%d",
            pool_address[:10], result["tick"], result["liquidity"], result["fee"],
        )
    except Exception as e:
        # On-chain call failures are non-fatal — the pool still appears in the
        # snapshot from API data; it just won't have live price/tick data.
        logger.warning("On-chain call failed for pool %s: %s", pool_address[:10], e)

    return result


# ---------------------------------------------------------------------------
# Pool pair classification
# ---------------------------------------------------------------------------

def classify_pool_pair(symbol: str) -> str:
    """
    Classify a pool's pair type from its symbol string.

    DeFiLlama returns symbols like "USDT-USDC", "BNB-USDT", "ETH-BTC".
    This function maps those to the four pair types used by the strategy
    profiles and decision engine.

    Classification rules (checked in order):
    1. Both tokens are stablecoins → "stable-stable"
    2. One token is stable, the other is large-cap → "stable-largecap"
    3. Both tokens are large-caps → "largecap-largecap"
    4. Anything else → "other"

    Args:
        symbol: Pair symbol string from DeFiLlama, e.g. "USDT-USDC".

    Returns:
        One of: 'stable-stable', 'stable-largecap', 'largecap-largecap', 'other'.
    """
    # Normalise: split on common separator characters, lowercase each token.
    parts = symbol.replace("/", "-").replace("_", "-").split("-")
    tokens = [p.strip().lower() for p in parts if p.strip()]

    if not tokens:
        return "other"

    def is_stable(t: str) -> bool:
        return any(s in t for s in STABLECOIN_SYMBOLS)

    def is_largecap(t: str) -> bool:
        return any(l in t for l in LARGECAP_SYMBOLS)

    stable_count = sum(1 for t in tokens if is_stable(t))
    largecap_count = sum(1 for t in tokens if is_largecap(t))

    if stable_count >= 2:
        return "stable-stable"
    if stable_count == 1 and largecap_count >= 1:
        return "stable-largecap"
    if stable_count == 0 and largecap_count >= 2:
        return "largecap-largecap"
    return "other"


# ---------------------------------------------------------------------------
# Pool validation
# ---------------------------------------------------------------------------

def _is_pool_valid(raw: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a raw DeFiLlama pool dict before including it in the snapshot.

    Rejects pools that would cause downstream errors or meaningless decisions:
    - Missing required fields
    - Zero or negative TVL (pool is effectively empty)
    - Negative APR (data quality issue)
    - Missing pool address

    Args:
        raw: Raw dict from the DeFiLlama API response.

    Returns:
        (True, "") if the pool passes all checks.
        (False, reason) if the pool should be excluded.
    """
    required = ("pool", "symbol", "tvlUsd")
    for key in required:
        if raw.get(key) is None:
            return False, f"missing required field '{key}'"

    if not raw.get("pool"):
        return False, "empty pool address"

    tvl = raw.get("tvlUsd", 0) or 0
    if tvl <= 0:
        return False, f"TVL is zero or negative ({tvl})"

    # APR fields may be None (no farming on this pool) — treat None as 0.
    apr_base = raw.get("apyBase") or 0
    apr_reward = raw.get("apyReward") or 0
    if apr_base < 0 or apr_reward < 0:
        return False, f"negative APR (base={apr_base}, reward={apr_reward})"

    return True, ""


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------

def build_pool_snapshot(
    raw_pools: List[Dict[str, Any]],
    prices: Dict[str, float],
    w3: Optional[Web3] = None,
    enrich_on_chain: bool = False,
) -> List[PoolData]:
    """
    Convert raw DeFiLlama pool dicts into validated PoolData objects.

    For each pool:
    1. Validate required fields and data quality.
    2. Normalise numeric fields (handle None → 0).
    3. Classify the pair type using the token symbol.
    4. Optionally enrich with on-chain slot0/liquidity data.

    The resulting list is sorted by total APR descending so callers can
    take `pools[:n]` to get the top-N pools without additional sorting.

    Args:
        raw_pools:       Filtered list from fetch_defi_llama_pools().
        prices:          Price dict from fetch_token_prices().
        w3:              Web3 instance for on-chain enrichment (optional).
        enrich_on_chain: If True, call fetch_on_chain_pool_data() for each
                         pool. This adds latency but gives live price/tick data.
                         Disabled by default for the main 15-second cycle to
                         keep cycle time short; enabled for /explore.

    Returns:
        Sorted list of validated PoolData objects.
    """
    pools: List[PoolData] = []
    rejected = 0

    for raw in raw_pools:
        valid, reason = _is_pool_valid(raw)
        if not valid:
            rejected += 1
            logger.debug("Rejected pool %s: %s", raw.get("pool", "?")[:10], reason)
            continue

        apr_base = float(raw.get("apyBase") or 0)
        apr_reward = float(raw.get("apyReward") or 0)
        tvl = float(raw.get("tvlUsd") or 0)
        # DeFiLlama may return volume as None for low-volume pools.
        volume = float(raw.get("volumeUsd1d") or 0)
        # Fee tier: DeFiLlama stores it as a fraction (e.g., 0.0001 for 0.01%).
        # Convert to basis points (integer) for consistency with on-chain values.
        fee_raw = raw.get("feeTier") or raw.get("fee") or 0
        try:
            fee_bps = int(float(fee_raw) * 1_000_000) if float(fee_raw) < 1 else int(fee_raw)
        except (TypeError, ValueError):
            fee_bps = 0

        pair_type = classify_pool_pair(raw.get("symbol", ""))

        pool_data = PoolData(
            pool=raw["pool"],
            symbol=raw.get("symbol", "Unknown"),
            apr=apr_base,
            apr_reward=apr_reward,
            tvl_usd=tvl,
            volume_24h=volume,
            fee_tier=fee_bps,
            pair_type=pair_type,
        )

        # Optional: enrich with on-chain data.
        if enrich_on_chain and w3 is not None:
            chain = fetch_on_chain_pool_data(w3, raw["pool"])
            if chain["success"]:
                pool_data.sqrt_price_x96 = chain["sqrt_price_x96"]
                pool_data.current_tick = chain["tick"]
                pool_data.on_chain_liquidity = chain["liquidity"]
                pool_data.chain_data_available = True
                # Prefer on-chain fee tier if it differs from API value.
                if chain["fee"] > 0:
                    pool_data.fee_tier = chain["fee"]

        pools.append(pool_data)

    logger.info(
        "Snapshot built: %d valid pools, %d rejected", len(pools), rejected
    )

    # Sort by total APR descending so top pools are always at pools[0].
    pools.sort(key=lambda p: p.total_apr(), reverse=True)
    return pools


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_market_snapshot(
    w3: Optional[Web3] = None,
    force_refresh: bool = False,
    enrich_on_chain: bool = False,
) -> MarketSnapshot:
    """
    Fetch, validate, and return a complete market snapshot.

    This is the ONLY function other modules should call for market data.
    It handles caching, API failures, and assembles all data sources into
    a single consistent MarketSnapshot object.

    Cache behaviour:
        Results are cached for SNAPSHOT_CACHE_SECONDS (30s) to avoid
        hitting DeFiLlama on every scheduler cycle. The cache is bypassed
        when force_refresh=True or when the cache has expired.

    Failure handling:
        - DeFiLlama failure: returns an empty pool list and records a warning.
        - Binance failure: uses stablecoin-only prices ($1 for USDT/USDC/BUSD).
        - On-chain failure: individual pool fields remain at defaults.
        Partial failures are recorded in snapshot.api_warnings.

    Args:
        w3:              Web3 instance for on-chain enrichment.
        force_refresh:   Bypass the cache and fetch fresh data.
        enrich_on_chain: Enrich each pool with on-chain slot0/liquidity data.
                         Adds ~0.1s per pool — use only for /explore, not cycles.

    Returns:
        A MarketSnapshot. Never raises — always returns something usable.
    """
    global _cached_snapshot, _cache_timestamp

    now = time.time()
    cache_age = now - _cache_timestamp
    if (
        not force_refresh
        and _cached_snapshot is not None
        and cache_age < SNAPSHOT_CACHE_SECONDS
    ):
        logger.debug("Returning cached snapshot (age: %.1fs)", cache_age)
        return _cached_snapshot

    warnings: List[str] = []
    start = time.monotonic()

    # ── Step 1: Fetch DeFiLlama pool data ────────────────────────────────
    raw_pools = fetch_defi_llama_pools()
    if not raw_pools:
        warnings.append("DeFiLlama fetch returned no pools — snapshot may be empty.")

    # ── Step 2: Fetch token prices ────────────────────────────────────────
    prices = fetch_token_prices()
    if not prices or prices.get("BNB", 0) == 0:
        warnings.append("Binance price fetch failed — BNB price unavailable.")

    # ── Step 3: Build validated pool list ────────────────────────────────
    pools = build_pool_snapshot(
        raw_pools=raw_pools,
        prices=prices,
        w3=w3,
        enrich_on_chain=enrich_on_chain,
    )

    elapsed = time.monotonic() - start
    logger.info(
        "Market snapshot ready: %d pools in %.2fs (BNB=$%.2f)",
        len(pools), elapsed, prices.get("BNB", 0),
    )

    snapshot = MarketSnapshot(
        pools=pools,
        prices=prices,
        api_warnings=warnings,
    )

    # Update cache.
    _cached_snapshot = snapshot
    _cache_timestamp = now

    return snapshot


# ---------------------------------------------------------------------------
# Cache control
# ---------------------------------------------------------------------------

def invalidate_cache() -> None:
    """
    Force the next get_market_snapshot() call to fetch fresh data.

    Called by the dispatcher at the start of a manual /allocate cycle so
    the user always sees up-to-date pool data when they explicitly request it.
    """
    global _cache_timestamp
    _cache_timestamp = 0.0
    logger.debug("Market snapshot cache invalidated.")
