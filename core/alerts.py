"""
core/alerts.py
==============
Watchlist alert checking for TGLP Bot.

Each scheduler cycle, the dispatcher calls check_all_alerts() after the
analysis step. This module iterates over session.watchlist, looks up each
item's current metric in the MarketSnapshot, and returns a list of Alert
objects for any thresholds that have been crossed.

Alerts are informational; they do not execute transactions. The dispatcher
sends a Telegram notification for each triggered alert.

Supported threshold types:
  - 'apr_above'      -- pool APR has risen above threshold_value %
  - 'apr_below'      -- pool APR has fallen below threshold_value %
  - 'tvl_below'      -- pool TVL (USD) has dropped below threshold_value
  - 'price_change_pct' -- BNB price has changed by ≥ threshold_value % since
                         the last cycle (token items with identifier 'BNB')

Watchlist items with an unrecognised threshold_type are skipped with a
warning log so future threshold types can be added without breaking existing
data.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.market_data import MarketSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """
    A single triggered watchlist alert.

    Attributes:
        watch_id:        Database row ID of the watchlist item that fired.
        chat_id:         Telegram chat ID of the alert's owner.
        item_type:       'pool' or 'token'.
        identifier:      Pool address or token symbol being watched.
        threshold_type:  The condition that was checked.
        threshold_value: The configured numeric threshold.
        current_value:   The metric's actual value this cycle.
        message:         Human-readable alert text for the Telegram notification.
    """
    watch_id: int
    chat_id: int
    item_type: str
    identifier: str
    threshold_type: str
    threshold_value: float
    current_value: float
    message: str


# ---------------------------------------------------------------------------
# Internal check helpers
# ---------------------------------------------------------------------------

def _check_pool_item(item: Dict, pool_data: Any) -> Optional[Alert]:
    """
    Check one 'pool' watchlist item against the current PoolData.

    Args:
        item:      Watchlist item dict (from session.watchlist).
        pool_data: PoolData object for the item's pool address.

    Returns:
        An Alert if the threshold is crossed, otherwise None.
    """
    tt = item["threshold_type"]
    tv = float(item["threshold_value"])
    ident = item["identifier"]
    wid = item["id"]
    chat = item["user_chat_id"]

    if tt == "apr_above":
        current = pool_data.apr
        if current > tv:
            return Alert(
                watch_id=wid,
                chat_id=chat,
                item_type="pool",
                identifier=ident,
                threshold_type=tt,
                threshold_value=tv,
                current_value=current,
                message=(
                    f"APR alert: {pool_data.symbol} APR is now {current:.2f}% "
                    f"(above your {tv:.2f}% threshold)."
                ),
            )

    elif tt == "apr_below":
        current = pool_data.apr
        if current < tv:
            return Alert(
                watch_id=wid,
                chat_id=chat,
                item_type="pool",
                identifier=ident,
                threshold_type=tt,
                threshold_value=tv,
                current_value=current,
                message=(
                    f"APR alert: {pool_data.symbol} APR has dropped to {current:.2f}% "
                    f"(below your {tv:.2f}% threshold)."
                ),
            )

    elif tt == "tvl_below":
        current = pool_data.tvl_usd
        if current < tv:
            return Alert(
                watch_id=wid,
                chat_id=chat,
                item_type="pool",
                identifier=ident,
                threshold_type=tt,
                threshold_value=tv,
                current_value=current,
                message=(
                    f"TVL alert: {pool_data.symbol} TVL is now "
                    f"${current:,.0f} (below your ${tv:,.0f} threshold)."
                ),
            )

    else:
        logger.warning(
            "Unknown threshold_type '%s' for watchlist item %d -- skipping.",
            tt, wid,
        )

    return None


# ---------------------------------------------------------------------------
# Public alert-checking functions
# ---------------------------------------------------------------------------

def check_pool_alerts(
    session: Any,
    snapshot: MarketSnapshot,
) -> List[Alert]:
    """
    Check all 'pool' watchlist items against the current market snapshot.

    Skips items whose pool address is not found in the snapshot (e.g., if the
    pool was removed from DeFiLlama's index). This is non-fatal: the alert
    stays on the watchlist and will fire when the pool reappears.

    Args:
        session:  UserSession, session.watchlist is read but not modified.
        snapshot: Current MarketSnapshot from get_market_snapshot().

    Returns:
        List of Alert objects for every threshold that was crossed this cycle.
        Empty list if nothing triggered.
    """
    triggered: List[Alert] = []

    for item in session.watchlist:
        if item.get("item_type") != "pool":
            continue

        pool_address = item.get("identifier", "")
        pool_data = snapshot.get_pool(pool_address)

        if pool_data is None:
            logger.debug(
                "Pool %s not found in snapshot -- watchlist item %d skipped.",
                pool_address, item.get("id"),
            )
            continue

        alert = _check_pool_item(item, pool_data)
        if alert is not None:
            triggered.append(alert)
            logger.info(
                "Alert triggered for chat_id %d: %s", session.chat_id, alert.message
            )

    return triggered


def check_price_alerts(
    session: Any,
    prices: Dict[str, float],
) -> List[Alert]:
    """
    Check 'token' watchlist items with threshold_type 'price_change_pct'.

    Compares the current BNB price against session.previous_bnb_price (set
    on the previous cycle). If the absolute percentage change is ≥ the
    threshold_value, an Alert is returned.

    Updates session.previous_bnb_price to the current price at the end so
    the next cycle has a fresh baseline.

    Args:
        session: UserSession, session.watchlist is read; session.previous_bnb_price
                 is both read and updated.
        prices:  Current token prices dict, e.g. {"BNB": 614.82}.

    Returns:
        List of Alert objects for every price-change threshold that fired.
    """
    triggered: List[Alert] = []
    current_bnb = prices.get("BNB", 0.0)

    for item in session.watchlist:
        if item.get("item_type") != "token":
            continue
        if item.get("threshold_type") != "price_change_pct":
            continue

        identifier = item.get("identifier", "").upper()
        if identifier != "BNB":
            logger.debug(
                "price_change_pct alert for unknown token '%s' (item %d) -- only BNB supported.",
                identifier, item.get("id"),
            )
            continue

        prev = session.previous_bnb_price
        if prev is None or prev == 0.0:
            # No previous price available; store current and skip.
            logger.debug(
                "price_change_pct alert %d: no previous BNB price -- storing baseline.",
                item.get("id"),
            )
            continue

        tv = float(item["threshold_value"])
        pct_change = abs((current_bnb - prev) / prev) * 100.0

        if pct_change >= tv:
            direction = "risen" if current_bnb > prev else "fallen"
            triggered.append(
                Alert(
                    watch_id=item["id"],
                    chat_id=item["user_chat_id"],
                    item_type="token",
                    identifier=identifier,
                    threshold_type="price_change_pct",
                    threshold_value=tv,
                    current_value=pct_change,
                    message=(
                        f"BNB price alert: BNB has {direction} by "
                        f"{pct_change:.2f}% (from ${prev:,.2f} to ${current_bnb:,.2f}), "
                        f"exceeding your {tv:.2f}% threshold."
                    ),
                )
            )
            logger.info(
                "Price alert triggered for chat_id %d: BNB moved %.2f%%",
                session.chat_id, pct_change,
            )

    # Update baseline for the next cycle regardless of whether alerts fired.
    if current_bnb > 0.0:
        session.previous_bnb_price = current_bnb

    return triggered


def check_all_alerts(
    session: Any,
    snapshot: MarketSnapshot,
    prices: Dict[str, float],
) -> List[Alert]:
    """
    Run all supported alert checks for a session.

    Checks:
      1. Pool-based alerts: APR above/below and TVL below thresholds.
      2. Token price-change alerts: BNB price movement >= threshold %.

    Args:
        session:  UserSession.
        snapshot: Current MarketSnapshot.
        prices:   Current token prices dict, e.g. {"BNB": 614.82}.

    Returns:
        Combined list of all triggered Alert objects.
    """
    alerts = check_pool_alerts(session, snapshot)
    alerts.extend(check_price_alerts(session, prices))
    return alerts


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_alert_message(alert: Alert) -> str:
    """
    Return the Telegram notification text for a triggered alert.

    The message field on the Alert dataclass already contains a human-readable
    description built when the alert was detected. This function simply wraps
    it with a consistent prefix so callers have a single formatting point.

    Args:
        alert: A triggered Alert from check_all_alerts().

    Returns:
        Plain-text string ready to send as a Telegram message.
    """
    return f"[ALERT] {alert.message}"
