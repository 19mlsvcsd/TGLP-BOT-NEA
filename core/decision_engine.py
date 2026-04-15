"""
core/decision_engine.py
=======================
Pool scoring and action decision logic for TGLP Bot.

This module answers one question each cycle: **what should the bot do next?**

It takes three inputs:
1. The current MarketSnapshot (from market_data.py)
2. The AnalysisResult for this cycle (from analyser.py)
3. The user's UserSession (strategy, current position, compound preference)

And produces one output: a `DecisionResult` that names the action (ALLOCATE,
REBALANCE, COMPOUND, or NO_ACTION), identifies the target pool, and includes
a human-readable reasoning string that is shown to the user.

Processing pipeline:
    pools → filter_pools_by_strategy() → score_pools() → make_decision()

Design: purely functional, no state, no side effects. The dispatcher calls
this module and then passes the result to executor.py if action is needed.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from config.settings import (
    GAS_LIMIT_ADD_LIQUIDITY,
    GAS_LIMIT_APPROVE,
    GAS_LIMIT_COLLECT,
    GAS_LIMIT_REMOVE_LIQUIDITY,
    GAS_LIMIT_SWAP,
    SCORE_WEIGHT_APR,
    SCORE_WEIGHT_STABILITY,
    SCORE_WEIGHT_TVL,
    SCORE_WEIGHT_VOLUME,
    StrategyConfig,
)
from core.analyser import AnalysisResult, PoolDelta, get_pool_stability_score
from core.market_data import PoolData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gas estimation reference price
# ---------------------------------------------------------------------------
# Used for pre-execution gas cost estimates displayed to the user.
# Conservative BSC mainnet figure; actual testnet cost will be lower.
# Stored in Gwei: 1 Gwei = 10^-9 BNB, so cost_bnb = units * price_wei / 10^18.
_GAS_PRICE_REFERENCE_GWEI: float = 5.0


# ---------------------------------------------------------------------------
# Decision enum
# ---------------------------------------------------------------------------

class Decision(Enum):
    """
    The four possible outcomes from one decision cycle.

    NO_ACTION:  Market is stable or no suitable pool found. Do nothing.
    ALLOCATE:   User has no open position. Enter a new LP position.
    REBALANCE:  A significantly better pool was found. Exit current, enter new.
    COMPOUND:   Position is healthy. Collect earned fees and reinvest them.
    """
    NO_ACTION = "no_action"
    ALLOCATE  = "allocate"
    REBALANCE = "rebalance"
    COMPOUND  = "compound"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScoredPool:
    """
    A PoolData enriched with its composite score and score component breakdown.

    Used by make_decision() to compare pools and by format_decision_summary()
    to display the scoring rationale to the user.

    Attributes:
        pool:           The underlying PoolData.
        score:          Composite weighted score in [0.0, 1.0].
        norm_apr:       Normalised APR component (0–1 within the scored set).
        norm_tvl:       Normalised TVL component.
        norm_stability: Normalised stability component.
        norm_volume:    Normalised volume component.
    """
    pool: PoolData
    score: float
    norm_apr: float
    norm_tvl: float
    norm_stability: float
    norm_volume: float

    def score_breakdown(self) -> str:
        """Return a compact one-line score breakdown string."""
        return (
            f"APR:{self.norm_apr:.2f} "
            f"TVL:{self.norm_tvl:.2f} "
            f"Stab:{self.norm_stability:.2f} "
            f"Vol:{self.norm_volume:.2f} "
            f"→ {self.score:.3f}"
        )


@dataclass
class DecisionResult:
    """
    The output of one decision cycle.

    Consumed by the dispatcher (Sprint 10) which either executes it directly
    (auto_execute=True) or sends it to the user as a proposal.

    Attributes:
        action:            What to do: ALLOCATE, REBALANCE, COMPOUND, NO_ACTION.
        target_pool:       The pool to act on, or None for NO_ACTION/COMPOUND.
        current_pool:      The pool currently held (for REBALANCE context).
        reasoning:         Human-readable explanation, shown to the user.
        scored_pools:      Top scored pools (up to 10) for display in proposals.
        estimated_gas_units: Total gas units for the full execution plan.
        estimated_gas_bnb:   Estimated cost in BNB at reference gas price.
        pools_considered:  How many pools were scored before this decision.
        pools_filtered:    How many pools were filtered out before scoring.
    """
    action: Decision
    target_pool: Optional[PoolData]
    reasoning: str
    scored_pools: List[ScoredPool] = field(default_factory=list)
    current_pool: Optional[PoolData] = None
    estimated_gas_units: int = 0
    estimated_gas_bnb: float = 0.0
    pools_considered: int = 0
    pools_filtered: int = 0


# ---------------------------------------------------------------------------
# Step 1: Filter pools by strategy
# ---------------------------------------------------------------------------

def filter_pools_by_strategy(
    pools: List[PoolData],
    strategy: StrategyConfig,
    analysis_result: Optional[AnalysisResult] = None,
) -> List[PoolData]:
    """
    Remove pools that do not meet the strategy's eligibility criteria.

    Filtering happens before scoring so that the normalisation step in
    score_pools() only considers comparable, eligible pools.

    Filters applied in order:
    1. Pair type must be in strategy.allowed_pair_types.
    2. TVL must be at or above strategy.min_tvl_usd.
    3. Pool must not appear in analysis_result.anomalous_addresses.

    Args:
        pools:           Full pool list from MarketSnapshot.
        strategy:        The user's active StrategyConfig.
        analysis_result: Optional, provides the anomalous address set.
                         If None (first run), no anomaly filtering is applied.

    Returns:
        Filtered list. May be empty if no pools pass all criteria.
    """
    anomalous = (
        analysis_result.anomalous_addresses
        if analysis_result is not None
        else set()
    )

    original_count = len(pools)
    result: List[PoolData] = []

    for pool in pools:
        # Filter 1: pair type
        if pool.pair_type not in strategy.allowed_pair_types:
            logger.debug(
                "Filter: %s excluded, pair type '%s' not in %s",
                pool.symbol, pool.pair_type, strategy.allowed_pair_types,
            )
            continue

        # Filter 2: minimum TVL
        if pool.tvl_usd < strategy.min_tvl_usd:
            logger.debug(
                "Filter: %s excluded, TVL $%.0f < min $%.0f",
                pool.symbol, pool.tvl_usd, strategy.min_tvl_usd,
            )
            continue

        # Filter 3: anomaly exclusion
        if pool.pool in anomalous:
            logger.debug("Filter: %s excluded, anomalous pool", pool.symbol)
            continue

        result.append(pool)

    logger.info(
        "filter_pools_by_strategy: %d → %d pools (%d filtered out)",
        original_count, len(result), original_count - len(result),
    )
    return result


# ---------------------------------------------------------------------------
# Step 2: Score pools
# ---------------------------------------------------------------------------

def score_pools(
    pools: List[PoolData],
    delta_history: Optional[List[PoolDelta]] = None,
) -> List[ScoredPool]:
    """
    Score a list of eligible pools using the composite weighted formula:

        score = (0.40 × norm_apr)
              + (0.30 × norm_tvl)
              + (0.20 × norm_stability)
              + (0.10 × norm_volume)

    Each metric is normalised to [0, 1] within the given pool set:
        norm_x = (x - x_min) / (x_max - x_min)

    When all pools share the same value for a metric (x_max == x_min), every
    pool receives 0.5 for that component so no pool is unfairly penalised.

    Stability scores come from get_pool_stability_score() using delta_history.
    Pools with no history receive a neutral 0.5 stability score.

    Args:
        pools:         Filtered pool list from filter_pools_by_strategy().
        delta_history: Recent PoolDelta history list for stability scoring.
                       Pass None or [] if no history is available.

    Returns:
        List of ScoredPool objects sorted by score descending.
        Returns an empty list if pools is empty.
    """
    if not pools:
        return []

    history = delta_history or []

    # ── Compute raw metric values for every pool ──────────────────────────
    raw_apr       = [p.total_apr() for p in pools]
    raw_tvl       = [p.tvl_usd for p in pools]
    raw_volume    = [p.volume_24h or 0.0 for p in pools]
    raw_stability = [
        get_pool_stability_score(p.pool, history) for p in pools
    ]

    # ── Min-max normalisation helper ──────────────────────────────────────
    def normalise(values: list) -> list:
        """
        Normalise a list of floats to [0, 1].
        If all values are equal, return 0.5 for all to avoid penalising any pool.
        """
        lo, hi = min(values), max(values)
        if hi == lo:
            return [0.5] * len(values)
        return [(v - lo) / (hi - lo) for v in values]

    norm_apr       = normalise(raw_apr)
    norm_tvl       = normalise(raw_tvl)
    norm_volume    = normalise(raw_volume)
    norm_stability = normalise(raw_stability)

    # ── Apply weighted formula and build ScoredPool objects ───────────────
    scored: List[ScoredPool] = []
    for i, pool in enumerate(pools):
        composite = (
            SCORE_WEIGHT_APR       * norm_apr[i]
            + SCORE_WEIGHT_TVL       * norm_tvl[i]
            + SCORE_WEIGHT_STABILITY * norm_stability[i]
            + SCORE_WEIGHT_VOLUME    * norm_volume[i]
        )
        scored.append(ScoredPool(
            pool=pool,
            score=composite,
            norm_apr=norm_apr[i],
            norm_tvl=norm_tvl[i],
            norm_stability=norm_stability[i],
            norm_volume=norm_volume[i],
        ))

    # Sort highest score first.
    scored.sort(key=lambda s: s.score, reverse=True)

    logger.debug(
        "score_pools: top pool is %s with score %.3f",
        scored[0].pool.symbol, scored[0].score,
    )
    return scored


# ---------------------------------------------------------------------------
# Gas estimation helper
# ---------------------------------------------------------------------------

def _estimate_gas(action: Decision) -> tuple[int, float]:
    """
    Estimate the total gas units and BNB cost for a given action.

    Uses conservative upper-bound gas limits from config/settings.py and
    a reference gas price of _GAS_PRICE_REFERENCE_GWEI. The actual cost on
    BSC Testnet will be much lower (0.1 Gwei vs. 5 Gwei reference).

    Args:
        action: The Decision enum value.

    Returns:
        Tuple of (gas_units: int, cost_bnb: float).
    """
    units = {
        Decision.ALLOCATE:  GAS_LIMIT_APPROVE + GAS_LIMIT_SWAP + GAS_LIMIT_ADD_LIQUIDITY,
        Decision.REBALANCE: (GAS_LIMIT_REMOVE_LIQUIDITY + GAS_LIMIT_COLLECT
                             + GAS_LIMIT_APPROVE + GAS_LIMIT_SWAP + GAS_LIMIT_ADD_LIQUIDITY),
        Decision.COMPOUND:  GAS_LIMIT_COLLECT + GAS_LIMIT_ADD_LIQUIDITY,
        Decision.NO_ACTION: 0,
    }.get(action, 0)

    # cost_bnb = units × price_gwei × 10^9 (wei/gwei) / 10^18 (BNB/wei)
    cost_bnb = units * _GAS_PRICE_REFERENCE_GWEI * 1e9 / 1e18
    return units, cost_bnb


# ---------------------------------------------------------------------------
# Step 3: Make decision
# ---------------------------------------------------------------------------

def make_decision(
    scored_pools: List[ScoredPool],
    current_position: Optional[Dict[str, Any]],
    strategy: StrategyConfig,
    analysis_result: Optional[AnalysisResult],
    compound_enabled: bool = False,
    fees_available: bool = False,
    pools_filtered_count: int = 0,
) -> DecisionResult:
    """
    Choose the best action given the scored pool list and current state.

    Decision tree (evaluated top-to-bottom, first match wins):

    1. NO_ACTION (early exit): no eligible pools after filtering.
    2. ALLOCATE: user has no open position → enter the top-scored pool.
    3. REBALANCE: user has a position, but a different pool scores higher
       by more than strategy.rebalance_threshold.
    4. COMPOUND: user has a position, no rebalance warranted, compounding
       is enabled, and fees are available to reinvest.
    5. NO_ACTION: none of the above conditions met.

    Args:
        scored_pools:        Output of score_pools(), sorted best first.
        current_position:    UserSession.current_position dict, or None.
        strategy:            The user's active StrategyConfig.
        analysis_result:     AnalysisResult from this cycle (may be None on first run).
        compound_enabled:    UserSession.compound_enabled flag.
        fees_available:      True if the position has earned fees to collect.
                             In Sprint 9, executor will check this on-chain.
                             Until then, the caller passes this flag.
        pools_filtered_count: Number of pools removed before scoring (for display).

    Returns:
        A DecisionResult with action, target pool, reasoning, and gas estimate.
    """
    pools_considered = len(scored_pools)

    # ── Case 1: No eligible pools ─────────────────────────────────────────
    if not scored_pools:
        gas_units, gas_bnb = _estimate_gas(Decision.NO_ACTION)
        return DecisionResult(
            action=Decision.NO_ACTION,
            target_pool=None,
            reasoning=(
                "No pools passed the strategy filter. "
                f"Strategy '{strategy.name}' requires {strategy.allowed_pair_types} pairs "
                f"with TVL ≥ ${strategy.min_tvl_usd:,.0f}. "
                "No action taken."
            ),
            scored_pools=[],
            estimated_gas_units=gas_units,
            estimated_gas_bnb=gas_bnb,
            pools_considered=0,
            pools_filtered=pools_filtered_count,
        )

    top = scored_pools[0]

    # ── Case 2: No open position → ALLOCATE ───────────────────────────────
    if current_position is None:
        gas_units, gas_bnb = _estimate_gas(Decision.ALLOCATE)
        return DecisionResult(
            action=Decision.ALLOCATE,
            target_pool=top.pool,
            reasoning=(
                f"No open position. Allocating to the highest-scored pool: "
                f"{top.pool.symbol} (score: {top.score:.3f}, "
                f"APR: {top.pool.total_apr():.2f}%, "
                f"TVL: ${top.pool.tvl_usd:,.0f})."
            ),
            scored_pools=scored_pools[:10],
            estimated_gas_units=gas_units,
            estimated_gas_bnb=gas_bnb,
            pools_considered=pools_considered,
            pools_filtered=pools_filtered_count,
        )

    # ── Locate the current pool in the scored list ────────────────────────
    current_pool_address = current_position.get("pool_address", "").lower()
    current_scored: Optional[ScoredPool] = None
    for sp in scored_pools:
        if sp.pool.pool.lower() == current_pool_address:
            current_scored = sp
            break

    # If the current pool was filtered out (e.g., became anomalous), treat
    # it as if it scores 0 to force a rebalance.
    current_score = current_scored.score if current_scored else 0.0
    current_pool_data = current_scored.pool if current_scored else None

    # ── Case 3: Better pool found → REBALANCE ─────────────────────────────
    # Rebalance if the top pool scores more than current + rebalance_threshold.
    # Using relative comparison: top must beat current by threshold fraction.
    score_gap = top.score - current_score
    rebalance_needed = (
        top.pool.pool.lower() != current_pool_address
        and score_gap > strategy.rebalance_threshold
    )

    if rebalance_needed:
        gas_units, gas_bnb = _estimate_gas(Decision.REBALANCE)
        improvement_pct = (score_gap / max(current_score, 0.001)) * 100
        return DecisionResult(
            action=Decision.REBALANCE,
            target_pool=top.pool,
            current_pool=current_pool_data,
            reasoning=(
                f"Rebalancing from {current_pool_data.symbol if current_pool_data else 'current pool'} "
                f"(score: {current_score:.3f}) to {top.pool.symbol} "
                f"(score: {top.score:.3f}). "
                f"Score improvement: {score_gap:.3f} ({improvement_pct:.1f}%), "
                f"exceeds threshold {strategy.rebalance_threshold:.0%}."
            ),
            scored_pools=scored_pools[:10],
            estimated_gas_units=gas_units,
            estimated_gas_bnb=gas_bnb,
            pools_considered=pools_considered,
            pools_filtered=pools_filtered_count,
        )

    # ── Case 4: Position healthy, compound if enabled ─────────────────────
    if compound_enabled and fees_available:
        gas_units, gas_bnb = _estimate_gas(Decision.COMPOUND)
        return DecisionResult(
            action=Decision.COMPOUND,
            target_pool=current_pool_data,
            current_pool=current_pool_data,
            reasoning=(
                f"Position in {current_pool_data.symbol if current_pool_data else 'current pool'} "
                f"is well-positioned (score: {current_score:.3f}). "
                "Compounding earned fees back into the position."
            ),
            scored_pools=scored_pools[:10],
            estimated_gas_units=gas_units,
            estimated_gas_bnb=gas_bnb,
            pools_considered=pools_considered,
            pools_filtered=pools_filtered_count,
        )

    # ── Case 5: No action warranted ───────────────────────────────────────
    reason_parts = [
        f"Position in {current_pool_data.symbol if current_pool_data else 'current pool'} "
        f"is performing well (score: {current_score:.3f})."
    ]
    if not rebalance_needed:
        reason_parts.append(
            f"Top pool {top.pool.symbol} scores {top.score:.3f}; "
            f"gap {score_gap:.3f} is below rebalance threshold {strategy.rebalance_threshold:.0%}."
        )
    if not compound_enabled:
        reason_parts.append("Auto-compounding is disabled.")
    elif not fees_available:
        reason_parts.append("No fees available to compound yet.")

    gas_units, gas_bnb = _estimate_gas(Decision.NO_ACTION)
    return DecisionResult(
        action=Decision.NO_ACTION,
        target_pool=None,
        current_pool=current_pool_data,
        reasoning=" ".join(reason_parts),
        scored_pools=scored_pools[:10],
        estimated_gas_units=gas_units,
        estimated_gas_bnb=gas_bnb,
        pools_considered=pools_considered,
        pools_filtered=pools_filtered_count,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_decision_summary(result: DecisionResult) -> str:
    """
    Format a DecisionResult as a MarkdownV2-ready Telegram message.

    Used by the dispatcher to notify the user of a proposed action
    (when auto_execute=False) or to log what the bot just did.

    Args:
        result: The DecisionResult from make_decision().

    Returns:
        MarkdownV2-formatted string, ready to send via Telegram.
    """
    from helpers.formatters import escape_md, format_bnb, format_pct, format_large_usd

    action_icons = {
        Decision.ALLOCATE:  "🟢",
        Decision.REBALANCE: "🔄",
        Decision.COMPOUND:  "♻️",
        Decision.NO_ACTION: "⚪",
    }
    icon = action_icons.get(result.action, "•")
    action_name = result.action.value.replace("_", " ").title()

    lines = [
        f"{icon} *{escape_md(action_name)}*",
        "",
        escape_md(result.reasoning),
    ]

    if result.target_pool and result.action != Decision.NO_ACTION:
        p = result.target_pool
        lines += [
            "",
            f"*Target pool:* {escape_md(p.symbol)}",
            f"APR: `{escape_md(format_pct(p.total_apr()))}`  TVL: `{escape_md(format_large_usd(p.tvl_usd))}`",
        ]

    if result.estimated_gas_bnb > 0:
        lines += [
            "",
            f"Est\\. gas: `{escape_md(format_bnb(result.estimated_gas_bnb))}` "
            f"\\({escape_md(str(result.estimated_gas_units))} units @ "
            f"{escape_md(str(_GAS_PRICE_REFERENCE_GWEI))} Gwei\\)",
        ]

    lines += [
        "",
        f"_Pools scored: {result.pools_considered} \\| Filtered: {result.pools_filtered}_",
    ]

    return "\n".join(lines)
