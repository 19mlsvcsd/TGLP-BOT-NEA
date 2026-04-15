"""
core/portfolio.py
=================
Portfolio valuation and P&L tracking for TGLP Bot.

This module converts raw position data (token amounts) into USD values and
computes the user's unrealised P&L relative to their entry cost. It is the
single place where "how much is my position worth?" and "am I up or down?"
are answered.

Design:
  - All public functions are pure (no side effects) except record_entry_value
    and record_gas_cost, which mutate the session in-memory only.
  - build_portfolio_summary() is the only function that makes a live RPC call
    (to read the wallet BNB balance). All other calculations use data already
    in the session or passed as arguments.
  - Token pricing is conservative: stablecoins are priced at $1, WBNB/BNB at
    the supplied bnb_price_usd, and unknown tokens at $0 (no oracle). This
    means the position value may be understated when exotic tokens are involved,
    but it will never be overstated.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from config.settings import STABLECOIN_SYMBOLS
from helpers.blockchain import get_bnb_balance

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PositionValue:
    """
    Estimated current value of an open LP position.

    Attributes:
        amount0:        Human-readable token0 amount held in the position.
        amount1:        Human-readable token1 amount held in the position.
        token0_symbol:  Ticker of token0, e.g. "WBNB".
        token1_symbol:  Ticker of token1, e.g. "USDT".
        value_usd:      Best estimate of total position value in USD.
                        Zero for tokens whose price is not determinable.
        value_bnb:      value_usd converted to BNB at bnb_price_used.
        bnb_price_used: The BNB/USD price used for conversion.
    """
    amount0: float
    amount1: float
    token0_symbol: str
    token1_symbol: str
    value_usd: float
    value_bnb: float
    bnb_price_used: float


@dataclass
class PnLResult:
    """
    Complete profit-and-loss breakdown for a user session.

    Unrealised P&L compares current position value against the USD cost at
    entry. Gas costs are deducted to produce the net P&L.

    Attributes:
        entry_value_usd:    USD value of tokens when the position was opened.
        current_value_usd:  Current estimated position value in USD.
        unrealised_pnl_usd: current_value_usd - entry_value_usd.
        unrealised_pnl_pct: Percentage change. 0.0 if entry_value_usd is zero.
        gas_spent_bnb:      Cumulative gas cost across all transactions (BNB).
        gas_cost_usd:       gas_spent_bnb converted to USD at current price.
        net_pnl_usd:        unrealised_pnl_usd - gas_cost_usd.
        rebalance_count:    Number of rebalances executed in this session.
    """
    entry_value_usd: float
    current_value_usd: float
    unrealised_pnl_usd: float
    unrealised_pnl_pct: float
    gas_spent_bnb: float
    gas_cost_usd: float
    net_pnl_usd: float
    rebalance_count: int


@dataclass
class PortfolioSummary:
    """
    Full portfolio snapshot for one user session.

    Attributes:
        has_position:    True if the session currently holds an LP position.
        position_value:  Current estimated position value. None if no position.
        pnl:             Full P&L breakdown.
        wallet_bnb:      Raw BNB balance of the user's wallet (live RPC read).
        wallet_usd:      wallet_bnb converted to USD.
    """
    has_position: bool
    position_value: Optional[PositionValue]
    pnl: PnLResult
    wallet_bnb: float
    wallet_usd: float


# ---------------------------------------------------------------------------
# Token pricing helper
# ---------------------------------------------------------------------------

def _token_usd_value(amount: float, symbol: str, bnb_price_usd: float) -> float:
    """
    Return the USD value of a token amount using conservative pricing.

    Pricing rules (in priority order):
      1. Stablecoins (USDT, USDC, BUSD, DAI, …)  → 1 USD per token
      2. WBNB or BNB                               → amount × bnb_price_usd
      3. Everything else                           → 0 USD (no oracle available)

    Args:
        amount:        Human-readable token amount (already divided by decimals).
        symbol:        Token ticker in any case, e.g. "WBNB", "usdt".
        bnb_price_usd: Current BNB/USD spot price.

    Returns:
        USD value as a float. Never negative.
    """
    sym = symbol.lower().strip()

    if sym in STABLECOIN_SYMBOLS:
        return amount  # 1:1 peg assumed

    if sym in ("bnb", "wbnb"):
        return amount * bnb_price_usd

    # Unknown token: return 0 rather than guessing.
    logger.debug(
        "No price available for token '%s' -- contribution set to $0.00", symbol
    )
    return 0.0


# ---------------------------------------------------------------------------
# Public valuation functions
# ---------------------------------------------------------------------------

def estimate_position_value(
    position: Dict[str, Any],
    bnb_price_usd: float,
) -> PositionValue:
    """
    Estimate the current USD value of an open LP position.

    The position dict is expected to contain at minimum:
      - amount0 (float): human-readable amount of token0 currently in the LP
      - amount1 (float): human-readable amount of token1 currently in the LP
      - token0_symbol (str): ticker for token0
      - token1_symbol (str): ticker for token1

    These fields are populated by executor.py when execute_allocate() succeeds
    and stored in session.current_position.

    Args:
        position:      The session.current_position dict.
        bnb_price_usd: Current BNB/USD spot price for WBNB valuation.

    Returns:
        PositionValue with all value fields populated.
    """
    amount0 = float(position.get("amount0", 0.0))
    amount1 = float(position.get("amount1", 0.0))
    sym0 = str(position.get("token0_symbol", ""))
    sym1 = str(position.get("token1_symbol", ""))

    value0 = _token_usd_value(amount0, sym0, bnb_price_usd)
    value1 = _token_usd_value(amount1, sym1, bnb_price_usd)
    total_usd = value0 + value1
    total_bnb = (total_usd / bnb_price_usd) if bnb_price_usd > 0 else 0.0

    return PositionValue(
        amount0=amount0,
        amount1=amount1,
        token0_symbol=sym0,
        token1_symbol=sym1,
        value_usd=total_usd,
        value_bnb=total_bnb,
        bnb_price_used=bnb_price_usd,
    )


def calculate_pnl(
    session: Any,
    current_value_usd: float,
    bnb_price_usd: float,
) -> PnLResult:
    """
    Calculate the full P&L breakdown for a session.

    Args:
        session:           UserSession from core/strategy_manager.py.
        current_value_usd: Current estimated position value in USD.
                           Pass 0.0 if there is no open position.
        bnb_price_usd:     Current BNB/USD price for gas cost conversion.

    Returns:
        PnLResult with all fields populated.
    """
    entry = session.entry_value_usd
    unrealised = current_value_usd - entry

    if entry > 0:
        unrealised_pct = (unrealised / entry) * 100.0
    else:
        unrealised_pct = 0.0

    gas_bnb = session.total_gas_spent_bnb
    gas_usd = gas_bnb * bnb_price_usd
    net_pnl = unrealised - gas_usd

    return PnLResult(
        entry_value_usd=entry,
        current_value_usd=current_value_usd,
        unrealised_pnl_usd=unrealised,
        unrealised_pnl_pct=unrealised_pct,
        gas_spent_bnb=gas_bnb,
        gas_cost_usd=gas_usd,
        net_pnl_usd=net_pnl,
        rebalance_count=session.rebalance_count,
    )


def record_entry_value(session: Any, value_usd: float) -> None:
    """
    Record the USD value of the position at the moment it was opened.

    Called by the dispatcher immediately after execute_allocate() succeeds.
    This value becomes the baseline for all subsequent P&L calculations.

    Args:
        session:   UserSession to update.
        value_usd: Entry cost in USD (typically from estimate_position_value
                   called right after the allocate transaction is confirmed).
    """
    session.entry_value_usd = value_usd
    logger.info(
        "Entry value recorded for chat_id %d: $%.2f",
        session.chat_id, value_usd,
    )


def record_gas_cost(session: Any, gas_cost_bnb: float) -> None:
    """
    Accumulate a gas cost into the session's lifetime gas total.

    Called by the dispatcher after every confirmed on-chain transaction
    (allocate, rebalance, compound, collect_fees).

    Args:
        session:       UserSession to update.
        gas_cost_bnb:  Gas cost for this transaction in BNB, from
                       ExecutionResult.gas_cost_bnb.
    """
    session.total_gas_spent_bnb += gas_cost_bnb
    logger.debug(
        "Gas cost recorded for chat_id %d: %.6f BNB (total: %.6f BNB)",
        session.chat_id, gas_cost_bnb, session.total_gas_spent_bnb,
    )


# ---------------------------------------------------------------------------
# Composite summary
# ---------------------------------------------------------------------------

def build_portfolio_summary(
    w3: Any,
    session: Any,
    bnb_price_usd: float,
) -> PortfolioSummary:
    """
    Assemble a complete portfolio snapshot for a session.

    Makes one live RPC call to read the wallet BNB balance. All other data
    comes from the session object or is computed locally.

    Args:
        w3:            Connected Web3 instance.
        session:       UserSession from core/strategy_manager.py.
        bnb_price_usd: Current BNB/USD spot price.

    Returns:
        PortfolioSummary with all fields populated. If the RPC call fails,
        wallet_bnb and wallet_usd are set to 0.0 and the error is logged.
    """
    # Live wallet balance.
    try:
        wallet_bnb = get_bnb_balance(w3, session.wallet_address)
    except Exception as exc:
        logger.warning(
            "Could not read wallet balance for chat_id %d: %s",
            session.chat_id, exc,
        )
        wallet_bnb = 0.0
    wallet_usd = wallet_bnb * bnb_price_usd

    # Position value.
    if session.has_position() and session.current_position:
        position_value = estimate_position_value(
            session.current_position, bnb_price_usd
        )
        current_value_usd = position_value.value_usd
    else:
        position_value = None
        current_value_usd = 0.0

    pnl = calculate_pnl(session, current_value_usd, bnb_price_usd)

    return PortfolioSummary(
        has_position=session.has_position(),
        position_value=position_value,
        pnl=pnl,
        wallet_bnb=wallet_bnb,
        wallet_usd=wallet_usd,
    )
