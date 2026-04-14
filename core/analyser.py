"""
core/analyser.py
================
Per-cycle delta analysis and anomaly detection for TGLP Bot.

Every time the scheduler fires, the dispatcher calls `analyse_cycle()` with
the current and previous MarketSnapshots. This module compares them and
produces an AnalysisResult that tells the decision engine:

1. Which pools changed and by how much (PoolDelta objects).
2. Which changes are anomalous and should be excluded from scoring.
3. Whether the overall market changed enough to warrant re-scoring.

Why anomaly detection matters:
    DeFiLlama data is aggregated and can contain occasional bad data points —
    a pool might show a 200% APR spike for one cycle before correcting. Acting
    on such data could cause unnecessary and costly rebalances. By flagging
    anomalies, we pass clean data to the decision engine and avoid reacting
    to noise.

Design: this module is purely functional — it takes two snapshots and returns
a result. It holds no state. The dispatcher is responsible for storing the
previous snapshot in UserSession.previous_snapshot.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config.settings import (
    ANOMALY_APR_SPIKE_THRESHOLD,
    ANOMALY_TVL_DROP_THRESHOLD,
    ANOMALY_PRICE_DEVIATION_THRESHOLD,
)
from core.market_data import MarketSnapshot, PoolData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Significance threshold
# ---------------------------------------------------------------------------
# A cycle is marked significant_change=True if any non-anomalous pool's APR
# moved by more than this many percentage points in absolute terms.
# Set conservatively low (0.5pp) so the decision engine re-evaluates often.
# The decision engine then applies the strategy-specific rebalance threshold
# to decide whether to actually act.
_SIGNIFICANCE_APR_CHANGE_PP: float = 0.5   # percentage points

# Stability score normaliser: a pool that changes this many APR percentage
# points per cycle gets a stability score of 0.0.
_STABILITY_NORMALISER_PP: float = 10.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PoolDelta:
    """
    Change metrics for one pool between two consecutive snapshots.

    All *_pct fields are expressed as fractions of the previous value:
    e.g., apr_change_pct = 0.25 means APR increased by 25% of its old value.
    apr_change_abs is expressed in percentage points:
    e.g., apr_change_abs = 2.5 means APR went from 10% to 12.5%.

    Attributes:
        pool_address:        Contract address of the pool.
        symbol:              Human-readable pair name (e.g., "USDT-USDC").
        apr_change_abs:      APR change in percentage points (+/-).
        apr_change_pct:      APR change relative to previous value (fraction).
        tvl_change_abs:      TVL change in USD (+/-).
        tvl_change_pct:      TVL change relative to previous value (fraction).
        volume_change_pct:   Volume change relative to previous value (fraction).
        is_anomalous:        True if any anomaly threshold was exceeded.
        anomaly_descriptions: Human-readable list of what triggered anomaly flags.
    """
    pool_address: str
    symbol: str
    apr_change_abs: float
    apr_change_pct: float
    tvl_change_abs: float
    tvl_change_pct: float
    volume_change_pct: float
    is_anomalous: bool = False
    anomaly_descriptions: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return a one-line human-readable summary of this delta."""
        flag = " ⚠️ ANOMALY" if self.is_anomalous else ""
        return (
            f"{self.symbol}: APR {self.apr_change_abs:+.2f}pp "
            f"({self.apr_change_pct*100:+.1f}%), "
            f"TVL {self.tvl_change_pct*100:+.1f}%{flag}"
        )


@dataclass
class AnalysisResult:
    """
    Output of one analysis cycle — produced by `analyse_cycle()`.

    Consumed by `core/decision_engine.py` to filter pools and decide action.

    Attributes:
        pool_deltas:       PoolDelta for every pool present in both snapshots.
        anomalies:         Flat list of anomaly description strings
                           (pool symbol + what triggered the flag).
        anomalous_addresses: Set of pool addresses flagged as anomalous.
                           Decision engine uses this to exclude pools.
        significant_change: True if any non-anomalous pool's APR shifted by
                           more than _SIGNIFICANCE_APR_CHANGE_PP since the
                           last cycle. Prompts the decision engine to re-score.
        first_run:         True when there was no previous snapshot — the bot
                           has just started. Decision engine will score all
                           pools fresh without comparing to a baseline.
        pools_compared:    Number of pools present in both snapshots.
        pools_new:         Pools in the current snapshot but not the previous.
        pools_dropped:     Pools in the previous snapshot but not the current.
    """
    pool_deltas: List[PoolDelta]
    anomalies: List[str]
    anomalous_addresses: set
    significant_change: bool
    first_run: bool
    pools_compared: int = 0
    pools_new: int = 0
    pools_dropped: int = 0

    def get_delta(self, pool_address: str) -> Optional[PoolDelta]:
        """Look up a PoolDelta by address. Returns None if not in this result."""
        addr_lower = pool_address.lower()
        for d in self.pool_deltas:
            if d.pool_address.lower() == addr_lower:
                return d
        return None

    def clean_pools(self, pool_list: list) -> list:
        """
        Filter a list of PoolData objects, removing any flagged as anomalous.

        Convenience method for the decision engine — call this before scoring.

        Args:
            pool_list: List of PoolData objects from a MarketSnapshot.

        Returns:
            Filtered list with anomalous pools removed.
        """
        return [p for p in pool_list if p.pool not in self.anomalous_addresses]


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def detect_anomalies(delta: PoolDelta) -> List[str]:
    """
    Check a PoolDelta against all anomaly thresholds.

    Returns a list of human-readable descriptions for each triggered threshold.
    An empty list means no anomalies were detected.

    Thresholds (from config/settings.py):
        - APR spike:    relative APR increase exceeds ANOMALY_APR_SPIKE_THRESHOLD
        - TVL drop:     relative TVL decrease exceeds ANOMALY_TVL_DROP_THRESHOLD
        Note: price deviation is checked separately using on-chain data when
        available (sqrtPriceX96 comparison). It is omitted here because the
        PoolDelta currently does not carry raw price values — the APR and TVL
        checks catch most bad-data scenarios.

    Args:
        delta: The PoolDelta to evaluate.

    Returns:
        List of anomaly description strings. Empty = clean.
    """
    descriptions: List[str] = []

    # APR spike: relative increase above threshold.
    # Only flag upward spikes — a falling APR is informative, not anomalous.
    if delta.apr_change_pct > ANOMALY_APR_SPIKE_THRESHOLD:
        descriptions.append(
            f"APR spike: +{delta.apr_change_pct*100:.1f}% relative increase "
            f"(threshold: +{ANOMALY_APR_SPIKE_THRESHOLD*100:.0f}%)"
        )

    # TVL drop: relative decrease below threshold.
    # Only flag downward drops — TVL increasing is not anomalous.
    if delta.tvl_change_pct < -ANOMALY_TVL_DROP_THRESHOLD:
        descriptions.append(
            f"TVL drop: {delta.tvl_change_pct*100:.1f}% relative decrease "
            f"(threshold: -{ANOMALY_TVL_DROP_THRESHOLD*100:.0f}%)"
        )

    return descriptions


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------

def analyse_cycle(
    current: MarketSnapshot,
    previous: Optional[MarketSnapshot],
) -> AnalysisResult:
    """
    Compare the current market snapshot to the previous one and produce deltas.

    This is the main entry point called by the dispatcher each cycle.
    It handles three cases:

    1. **First run** (previous is None): returns an AnalysisResult with
       first_run=True and empty deltas. The decision engine treats this
       as a clean slate and scores all pools fresh.

    2. **Normal cycle**: computes PoolDelta for every pool present in both
       snapshots, runs anomaly detection on each delta, and sets
       significant_change if any clean pool's APR shifted enough to matter.

    3. **New / dropped pools**: pools that appear in one snapshot but not the
       other are counted in pools_new / pools_dropped but do not produce
       PoolDelta objects (no baseline to compare against).

    Args:
        current:  The freshly-fetched MarketSnapshot.
        previous: The snapshot from the last cycle, or None on first run.

    Returns:
        An AnalysisResult describing what changed between the two snapshots.
    """
    # ── Case 1: first run ─────────────────────────────────────────────────
    if previous is None:
        logger.info(
            "analyse_cycle: first run — %d pools, no previous snapshot.",
            len(current.pools),
        )
        return AnalysisResult(
            pool_deltas=[],
            anomalies=[],
            anomalous_addresses=set(),
            significant_change=False,
            first_run=True,
            pools_compared=0,
            pools_new=len(current.pools),
            pools_dropped=0,
        )

    # ── Case 2: normal cycle — build address lookup dicts ─────────────────
    prev_by_addr: Dict[str, PoolData] = {
        p.pool.lower(): p for p in previous.pools
    }
    curr_by_addr: Dict[str, PoolData] = {
        p.pool.lower(): p for p in current.pools
    }

    current_addresses = set(curr_by_addr.keys())
    previous_addresses = set(prev_by_addr.keys())

    pools_new = len(current_addresses - previous_addresses)
    pools_dropped = len(previous_addresses - current_addresses)
    compared_addresses = current_addresses & previous_addresses

    # ── Compute deltas for pools present in both snapshots ─────────────────
    pool_deltas: List[PoolDelta] = []
    all_anomalies: List[str] = []
    anomalous_addresses: set = set()
    significant_change = False

    for addr in compared_addresses:
        curr_pool = curr_by_addr[addr]
        prev_pool = prev_by_addr[addr]

        # APR delta — absolute (percentage points) and relative (fraction).
        apr_prev = prev_pool.total_apr()
        apr_curr = curr_pool.total_apr()
        apr_change_abs = apr_curr - apr_prev
        # Relative change: protect against division by zero when prev APR is 0.
        apr_change_pct = (
            (apr_curr - apr_prev) / apr_prev if apr_prev != 0 else 0.0
        )

        # TVL delta.
        tvl_prev = prev_pool.tvl_usd
        tvl_curr = curr_pool.tvl_usd
        tvl_change_abs = tvl_curr - tvl_prev
        tvl_change_pct = (
            (tvl_curr - tvl_prev) / tvl_prev if tvl_prev != 0 else 0.0
        )

        # Volume delta (24h volume may be None → treat as 0).
        vol_prev = prev_pool.volume_24h or 0.0
        vol_curr = curr_pool.volume_24h or 0.0
        volume_change_pct = (
            (vol_curr - vol_prev) / vol_prev if vol_prev != 0 else 0.0
        )

        delta = PoolDelta(
            pool_address=curr_pool.pool,
            symbol=curr_pool.symbol,
            apr_change_abs=apr_change_abs,
            apr_change_pct=apr_change_pct,
            tvl_change_abs=tvl_change_abs,
            tvl_change_pct=tvl_change_pct,
            volume_change_pct=volume_change_pct,
        )

        # Run anomaly detection on this delta.
        anomaly_descriptions = detect_anomalies(delta)
        if anomaly_descriptions:
            delta.is_anomalous = True
            delta.anomaly_descriptions = anomaly_descriptions
            anomalous_addresses.add(curr_pool.pool)
            for desc in anomaly_descriptions:
                all_anomalies.append(f"{curr_pool.symbol}: {desc}")
            logger.warning(
                "Anomaly detected in %s: %s",
                curr_pool.symbol, "; ".join(anomaly_descriptions),
            )

        # Check significance: if this pool is clean and its APR moved enough,
        # flag the cycle so the decision engine re-evaluates.
        if (
            not delta.is_anomalous
            and abs(apr_change_abs) >= _SIGNIFICANCE_APR_CHANGE_PP
        ):
            significant_change = True

        pool_deltas.append(delta)

    logger.info(
        "analyse_cycle: %d compared, %d new, %d dropped, %d anomalies, significant=%s",
        len(compared_addresses), pools_new, pools_dropped,
        len(anomalous_addresses), significant_change,
    )

    return AnalysisResult(
        pool_deltas=pool_deltas,
        anomalies=all_anomalies,
        anomalous_addresses=anomalous_addresses,
        significant_change=significant_change,
        first_run=False,
        pools_compared=len(compared_addresses),
        pools_new=pools_new,
        pools_dropped=pools_dropped,
    )


# ---------------------------------------------------------------------------
# Pool stability scoring
# ---------------------------------------------------------------------------

def get_pool_stability_score(
    pool_address: str,
    delta_history: List[PoolDelta],
) -> float:
    """
    Calculate a stability score for a pool based on its recent delta history.

    Stability reflects how consistent the pool's APR has been over recent cycles.
    A pool whose APR barely moves is predictable and reliable; one with large
    swings is harder to score confidently and carries higher rebalancing risk.

    The score is used as one of four inputs by the decision engine's scoring
    formula (weight: 0.20 from config/settings.SCORE_WEIGHT_STABILITY).

    Algorithm:
        1. Collect the absolute APR changes for this pool from delta_history.
        2. Compute the mean absolute APR change (in percentage points).
        3. Normalise against _STABILITY_NORMALISER_PP (10pp per cycle = score 0).
        4. Clamp to [0.0, 1.0].

    A pool with no history (delta_history is empty or has no matching entries)
    receives a neutral score of 0.5 — neither rewarded nor penalised.

    Args:
        pool_address:  Pool contract address to look up in the history.
        delta_history: List of PoolDelta objects from past cycles. Typically
                       the last 10–20 cycles from the dispatcher's rolling buffer.

    Returns:
        Stability score in [0.0, 1.0]. Higher = more stable.
    """
    addr_lower = pool_address.lower()

    # Filter to deltas for this pool only.
    relevant = [
        d for d in delta_history
        if d.pool_address.lower() == addr_lower and not d.is_anomalous
    ]

    if not relevant:
        # No history → neutral score.
        return 0.5

    # Mean absolute APR change across recorded cycles.
    mean_abs_change = sum(abs(d.apr_change_abs) for d in relevant) / len(relevant)

    # Normalise: 0pp change → 1.0, _STABILITY_NORMALISER_PP change → 0.0.
    score = 1.0 - (mean_abs_change / _STABILITY_NORMALISER_PP)

    # Clamp to valid range.
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Price deviation check (on-chain data)
# ---------------------------------------------------------------------------

def check_price_deviation(
    current: PoolData,
    previous: PoolData,
) -> Optional[str]:
    """
    Check whether a pool's on-chain price deviated anomalously between cycles.

    This supplemental check is only run when both snapshots have on-chain
    sqrtPriceX96 data (chain_data_available=True on both). It calculates the
    price change from the ratio of sqrtPriceX96 values.

    The sqrtPriceX96 encodes the current price as:
        price = (sqrtPriceX96 / 2^96) ** 2

    So the price ratio between cycles is:
        price_ratio = (sqrt_curr / sqrt_prev) ** 2

    Args:
        current:  Current cycle's PoolData.
        previous: Previous cycle's PoolData.

    Returns:
        Anomaly description string if the threshold was exceeded, else None.
    """
    if not (current.chain_data_available and previous.chain_data_available):
        return None

    sqrt_curr = current.sqrt_price_x96
    sqrt_prev = previous.sqrt_price_x96

    if not sqrt_curr or not sqrt_prev or sqrt_prev == 0:
        return None

    # Price is proportional to (sqrtPrice)^2, so price_change_pct uses the
    # ratio of squares — equivalently the square of the sqrtPrice ratio.
    price_ratio = (sqrt_curr / sqrt_prev) ** 2
    price_change_pct = abs(price_ratio - 1.0)

    if price_change_pct > ANOMALY_PRICE_DEVIATION_THRESHOLD:
        return (
            f"Price deviation: {price_change_pct*100:.1f}% in one cycle "
            f"(threshold: {ANOMALY_PRICE_DEVIATION_THRESHOLD*100:.0f}%)"
        )
    return None
