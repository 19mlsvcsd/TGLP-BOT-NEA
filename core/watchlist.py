"""
core/watchlist.py
=================
Watchlist management for TGLP Bot.

This module bridges the in-memory watchlist (session.watchlist) with the
SQLite watchlist table. Every write is committed to the database immediately
so that the watchlist survives a restart — unlike private key data which is
deliberately never persisted.

On startup, load_watchlist() must be called once per session to populate
session.watchlist from the database. After that, callers work with the
in-memory list and this module keeps the database in sync.

Supported threshold types (used by core/alerts.py):
  - 'apr_above'  -- alert when pool APR rises above threshold_value %
  - 'apr_below'  -- alert when pool APR falls below threshold_value %
  - 'tvl_below'  -- alert when pool TVL drops below threshold_value USD
"""

import logging
from typing import Any, Dict, List, Optional

from helpers.database import (
    deactivate_watchlist_item,
    get_active_watchlist,
    insert_watchlist_item,
)

logger = logging.getLogger(__name__)

# Maximum number of watchlist items a user can hold.
# Keeps the alert-checking loop bounded.
MAX_WATCHLIST_ITEMS: int = 20


def load_watchlist(session: Any) -> None:
    """
    Load all active watchlist items from the database into the session.

    Replaces the in-memory session.watchlist list with a fresh copy from
    SQLite. Call this once per session during onboarding or restart.

    Args:
        session: UserSession — session.watchlist is overwritten in place.
    """
    items = get_active_watchlist(session.chat_id)
    session.watchlist = items
    logger.debug(
        "Loaded %d watchlist item(s) for chat_id %d",
        len(items), session.chat_id,
    )


def add_watch_item(
    session: Any,
    item_type: str,
    identifier: str,
    threshold_type: str,
    threshold_value: float,
) -> int:
    """
    Add a new watchlist alert for the user.

    Writes to the database first, then appends to session.watchlist on success.
    If the user already has MAX_WATCHLIST_ITEMS active items, the call is
    rejected and -1 is returned.

    Args:
        session:         UserSession — session.watchlist is updated in place.
        item_type:       'pool' or 'token'.
        identifier:      Pool address (for item_type='pool') or token symbol
                         (for item_type='token').
        threshold_type:  Alert condition: 'apr_above', 'apr_below', 'tvl_below'.
        threshold_value: Numeric threshold value that triggers the alert.

    Returns:
        The database row ID of the new item (positive integer), or -1 on failure.
    """
    if len(session.watchlist) >= MAX_WATCHLIST_ITEMS:
        logger.warning(
            "Watchlist full for chat_id %d (limit %d).",
            session.chat_id, MAX_WATCHLIST_ITEMS,
        )
        return -1

    watch_id = insert_watchlist_item(
        user_chat_id=session.chat_id,
        item_type=item_type,
        identifier=identifier,
        threshold_type=threshold_type,
        threshold_value=threshold_value,
    )

    if watch_id > 0:
        session.watchlist.append({
            "id": watch_id,
            "user_chat_id": session.chat_id,
            "item_type": item_type,
            "identifier": identifier,
            "threshold_type": threshold_type,
            "threshold_value": threshold_value,
            "active": 1,
        })
        logger.info(
            "Watchlist item %d added for chat_id %d: %s %s %s %.4f",
            watch_id, session.chat_id, item_type, identifier,
            threshold_type, threshold_value,
        )
    else:
        logger.error(
            "Failed to insert watchlist item for chat_id %d.", session.chat_id
        )

    return watch_id


def remove_watch_item(session: Any, watch_id: int) -> bool:
    """
    Remove a watchlist item (soft delete: sets active=0 in the database).

    Removes the item from the in-memory session.watchlist only if the database
    update succeeds. The user_chat_id check in the database layer prevents one
    user from deleting another user's items.

    Args:
        session:  UserSession — session.watchlist is updated in place.
        watch_id: Database row ID of the item to remove.

    Returns:
        True if the item was found and deactivated, False otherwise.
    """
    ok = deactivate_watchlist_item(watch_id, session.chat_id)
    if ok:
        before = len(session.watchlist)
        session.watchlist = [
            w for w in session.watchlist if w.get("id") != watch_id
        ]
        after = len(session.watchlist)
        logger.info(
            "Watchlist item %d removed for chat_id %d (%d --> %d items).",
            watch_id, session.chat_id, before, after,
        )
    else:
        logger.warning(
            "Watchlist item %d not found or not owned by chat_id %d.",
            watch_id, session.chat_id,
        )
    return ok


def get_watch_item(session: Any, watch_id: int) -> Optional[Dict]:
    """
    Look up a single watchlist item by its database ID.

    Searches the in-memory session.watchlist — no database call needed.

    Args:
        session:  UserSession.
        watch_id: Database row ID to look up.

    Returns:
        The watchlist item dict, or None if not found.
    """
    for item in session.watchlist:
        if item.get("id") == watch_id:
            return item
    return None


def count_watch_items(session: Any) -> int:
    """
    Return the number of active watchlist items for a session.

    Args:
        session: UserSession.

    Returns:
        Count of items in session.watchlist.
    """
    return len(session.watchlist)
