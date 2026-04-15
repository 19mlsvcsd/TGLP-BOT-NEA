"""
helpers/database.py
===================
SQLite database layer for TGLP Bot.

Provides the database schema, initialisation, and helper functions for
persisting data across bot restarts. Three tables are managed here:

- logs:      Structured application logs (info, warning, error events).
- trades:    Full history of every on-chain transaction executed by the bot.
- watchlist: User-configured pool/token watch items with alert thresholds.

The database file is created automatically on first run in the project root.
Private keys and sensitive wallet data are NEVER stored in the database.

Design note: we use the sqlite3 standard library (no ORM) to keep dependencies
minimal and make the data structure transparent for NEA documentation.
"""

import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.settings import DB_FILENAME

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

# SQL for creating the logs table.
# Each row records one application event with severity level and optional
# JSON-serialisable context string for structured debugging.
_CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    level       TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    context     TEXT
);
"""

# SQL for creating the trades table.
# Each row represents one on-chain transaction (swap, LP mint, LP burn,
# collect fees). The tx_hash is a clickable link to BSCScan Testnet.
_CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    user_chat_id    INTEGER NOT NULL,
    action_type     TEXT    NOT NULL,
    pool_address    TEXT,
    token_in        TEXT,
    token_out       TEXT,
    amount_in       TEXT,
    amount_out      TEXT,
    tx_hash         TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    gas_used        INTEGER,
    gas_cost_bnb    TEXT
);
"""

# SQL for creating the watchlist table.
# Each row is one item the user wants to monitor. item_type is either
# 'pool' or 'token'. threshold_type describes what is being watched
# (e.g., 'apr_below', 'price_change_pct'). active=1 means the alert
# is still live; active=0 means it has been triggered or removed.
_CREATE_WATCHLIST_TABLE = """
CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_chat_id    INTEGER NOT NULL,
    item_type       TEXT    NOT NULL,
    identifier      TEXT    NOT NULL,
    threshold_type  TEXT    NOT NULL,
    threshold_value REAL    NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_connection(db_path: str = DB_FILENAME) -> sqlite3.Connection:
    """
    Open a connection to the SQLite database file.

    Creates the file if it does not exist. Enables WAL journal mode for
    better concurrent read performance and sets row_factory so that query
    results are returned as dictionaries rather than plain tuples.

    Args:
        db_path: Path to the .db file. Defaults to DB_FILENAME in settings.

    Returns:
        An open sqlite3.Connection object. Caller is responsible for closing.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # rows accessible as dicts: row["column"]
    # WAL mode allows reads while a write is in progress, which is important for the
    # scheduler running cycles while the bot handles user commands concurrently.
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def initialise_database(db_path: str = DB_FILENAME) -> None:
    """
    Create all tables if they do not already exist.

    Safe to call on every startup; uses CREATE TABLE IF NOT EXISTS, so
    existing data is never wiped. Should be called once at application start
    before any other database operations.

    Args:
        db_path: Path to the database file.
    """
    try:
        conn = get_connection(db_path)
        with conn:
            conn.execute(_CREATE_LOGS_TABLE)
            conn.execute(_CREATE_TRADES_TABLE)
            conn.execute(_CREATE_WATCHLIST_TABLE)
        conn.close()
        logger.info("Database initialised at %s", db_path)
    except sqlite3.Error as e:
        # This is a critical startup failure; re-raise so main.py can handle it.
        logger.critical("Failed to initialise database: %s", e)
        raise


# ---------------------------------------------------------------------------
# Logs table helpers
# ---------------------------------------------------------------------------

def insert_log(
    level: str,
    message: str,
    context: Optional[str] = None,
    db_path: str = DB_FILENAME,
) -> None:
    """
    Write one log entry to the logs table.

    This supplements (does not replace) the standard Python logging system.
    Writing to SQLite allows the bot to display recent events in /dashboard
    and persist them across restarts.

    Args:
        level:    Severity string: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'.
        message:  Human-readable description of the event.
        context:  Optional additional data (e.g., serialised dict as a JSON string).
        db_path:  Path to the database file.
    """
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    try:
        conn = get_connection(db_path)
        with conn:
            conn.execute(
                "INSERT INTO logs (timestamp, level, message, context) VALUES (?, ?, ?, ?)",
                (timestamp, level.upper(), message, context),
            )
        conn.close()
    except sqlite3.Error as e:
        # Log to Python logger only, to avoid infinite recursion.
        logger.error("Failed to write log to database: %s", e)


def get_recent_logs(
    limit: int = 50,
    level_filter: Optional[str] = None,
    db_path: str = DB_FILENAME,
) -> List[Dict[str, Any]]:
    """
    Retrieve recent log entries, newest first.

    Args:
        limit:        Maximum number of rows to return.
        level_filter: If provided, only return rows with this severity level.
        db_path:      Path to the database file.

    Returns:
        List of dicts with keys: id, timestamp, level, message, context.
    """
    try:
        conn = get_connection(db_path)
        if level_filter:
            rows = conn.execute(
                "SELECT * FROM logs WHERE level = ? ORDER BY id DESC LIMIT ?",
                (level_filter.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error("Failed to query logs: %s", e)
        return []


# ---------------------------------------------------------------------------
# Trades table helpers
# ---------------------------------------------------------------------------

def insert_trade(
    user_chat_id: int,
    action_type: str,
    pool_address: Optional[str] = None,
    token_in: Optional[str] = None,
    token_out: Optional[str] = None,
    amount_in: Optional[str] = None,
    amount_out: Optional[str] = None,
    tx_hash: Optional[str] = None,
    status: str = "pending",
    gas_used: Optional[int] = None,
    gas_cost_bnb: Optional[str] = None,
    db_path: str = DB_FILENAME,
) -> int:
    """
    Record a new trade/transaction in the database.

    Called by executor.py immediately when an action begins (status='pending'),
    then updated to 'confirmed' or 'failed' once the receipt is received.

    Args:
        user_chat_id:  Telegram chat ID of the user who owns this trade.
        action_type:   One of: 'approve', 'swap', 'add_liquidity',
                       'remove_liquidity', 'collect_fees', 'compound'.
        pool_address:  Address of the target LP pool (if applicable).
        token_in:      Symbol of the token being sold/deposited.
        token_out:     Symbol of the token being bought/received.
        amount_in:     Human-readable amount of token_in.
        amount_out:    Human-readable amount of token_out.
        tx_hash:       Transaction hash once broadcast (None until then).
        status:        'pending', 'confirmed', or 'failed'.
        gas_used:      Actual gas units consumed (from tx receipt).
        gas_cost_bnb:  Gas cost in BNB as a string (e.g., "0.000213").
        db_path:       Path to the database file.

    Returns:
        The auto-incremented row ID of the inserted trade record.
    """
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    try:
        conn = get_connection(db_path)
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO trades
                    (timestamp, user_chat_id, action_type, pool_address,
                     token_in, token_out, amount_in, amount_out,
                     tx_hash, status, gas_used, gas_cost_bnb)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, user_chat_id, action_type, pool_address,
                    token_in, token_out, amount_in, amount_out,
                    tx_hash, status, gas_used, gas_cost_bnb,
                ),
            )
            trade_id = cursor.lastrowid
        conn.close()
        return trade_id
    except sqlite3.Error as e:
        logger.error("Failed to insert trade: %s", e)
        return -1


def update_trade_status(
    trade_id: int,
    status: str,
    tx_hash: Optional[str] = None,
    gas_used: Optional[int] = None,
    gas_cost_bnb: Optional[str] = None,
    amount_out: Optional[str] = None,
    db_path: str = DB_FILENAME,
) -> bool:
    """
    Update the status and receipt data of an existing trade record.

    Called by executor.py after a transaction is mined to record the outcome.

    Args:
        trade_id:     The row ID returned by insert_trade().
        status:       New status: 'confirmed' or 'failed'.
        tx_hash:      Transaction hash (if not set during insert).
        gas_used:     Actual gas units from the receipt.
        gas_cost_bnb: Actual gas cost in BNB.
        amount_out:   Actual output amount (may differ from estimate).
        db_path:      Path to the database file.

    Returns:
        True if the update succeeded, False otherwise.
    """
    try:
        conn = get_connection(db_path)
        with conn:
            conn.execute(
                """
                UPDATE trades SET
                    status       = ?,
                    tx_hash      = COALESCE(?, tx_hash),
                    gas_used     = COALESCE(?, gas_used),
                    gas_cost_bnb = COALESCE(?, gas_cost_bnb),
                    amount_out   = COALESCE(?, amount_out)
                WHERE id = ?
                """,
                (status, tx_hash, gas_used, gas_cost_bnb, amount_out, trade_id),
            )
        conn.close()
        return True
    except sqlite3.Error as e:
        logger.error("Failed to update trade %d: %s", trade_id, e)
        return False


def _date_cutoff(since_days: int) -> str:
    """
    Return an ISO-format timestamp string for `since_days` days ago (UTC).

    SQLite stores timestamps as ISO strings, which sort lexicographically in
    the same order as chronologically, so a ``timestamp >= cutoff`` WHERE
    clause gives a correct date-range filter without a date-aware SQL function.

    Args:
        since_days: Number of days to look back (e.g., 7 or 30).

    Returns:
        ISO-format string, e.g. ``"2026-04-08T12:30:00"``.
    """
    from datetime import timedelta
    return (datetime.utcnow() - timedelta(days=since_days)).isoformat(timespec="seconds")


def get_trades_for_user(
    user_chat_id: int,
    limit: int = 20,
    offset: int = 0,
    action_filter: Optional[str] = None,
    since_days: Optional[int] = None,
    db_path: str = DB_FILENAME,
) -> List[Dict[str, Any]]:
    """
    Retrieve a paginated list of trades for a specific user.

    Args:
        user_chat_id:  Telegram chat ID to filter by.
        limit:         Number of rows per page (for /history pagination).
        offset:        Row offset (page * limit).
        action_filter: If provided, only return rows of this action_type.
        since_days:    If provided, only return rows from the last N days.
        db_path:       Path to the database file.

    Returns:
        List of trade dicts, newest first.
    """
    try:
        conn = get_connection(db_path)

        # Build WHERE conditions dynamically to handle all filter combinations.
        conditions = ["user_chat_id = ?"]
        params: list = [user_chat_id]

        if action_filter:
            conditions.append("action_type = ?")
            params.append(action_filter)

        if since_days is not None:
            conditions.append("timestamp >= ?")
            params.append(_date_cutoff(since_days))

        where = " AND ".join(conditions)
        params.extend([limit, offset])

        rows = conn.execute(
            f"SELECT * FROM trades WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error("Failed to query trades for user %d: %s", user_chat_id, e)
        return []


def get_all_trades_for_user(
    user_chat_id: int,
    db_path: str = DB_FILENAME,
) -> List[Dict[str, Any]]:
    """
    Retrieve every trade for a user, used by /export.

    Args:
        user_chat_id: Telegram chat ID.
        db_path:      Path to the database file.

    Returns:
        All trade rows for this user, oldest first.
    """
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_chat_id = ? ORDER BY id ASC",
            (user_chat_id,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error("Failed to export trades for user %d: %s", user_chat_id, e)
        return []


def count_trades_for_user(
    user_chat_id: int,
    action_filter: Optional[str] = None,
    since_days: Optional[int] = None,
    db_path: str = DB_FILENAME,
) -> int:
    """
    Return the total number of trade records for a user.

    Used to calculate the total number of history pages.

    Args:
        user_chat_id:  Telegram chat ID.
        action_filter: If provided, only count rows of this action_type.
        since_days:    If provided, only count rows from the last N days.
        db_path:       Path to the database file.

    Returns:
        Integer count of trade rows.
    """
    try:
        conn = get_connection(db_path)

        conditions = ["user_chat_id = ?"]
        params: list = [user_chat_id]

        if action_filter:
            conditions.append("action_type = ?")
            params.append(action_filter)

        if since_days is not None:
            conditions.append("timestamp >= ?")
            params.append(_date_cutoff(since_days))

        where = " AND ".join(conditions)
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM trades WHERE {where}",
            params,
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except sqlite3.Error as e:
        logger.error("Failed to count trades for user %d: %s", user_chat_id, e)
        return 0


# ---------------------------------------------------------------------------
# Watchlist table helpers
# ---------------------------------------------------------------------------

def insert_watchlist_item(
    user_chat_id: int,
    item_type: str,
    identifier: str,
    threshold_type: str,
    threshold_value: float,
    db_path: str = DB_FILENAME,
) -> int:
    """
    Add a new watchlist alert item for a user.

    Args:
        user_chat_id:    Telegram chat ID.
        item_type:       'pool' or 'token'.
        identifier:      Pool address or token symbol being watched.
        threshold_type:  What to check, e.g., 'apr_below', 'apr_above',
                         'price_change_pct', 'tvl_below'.
        threshold_value: Numeric threshold that triggers the alert.
        db_path:         Path to the database file.

    Returns:
        The row ID of the new watchlist item, or -1 on failure.
    """
    try:
        conn = get_connection(db_path)
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO watchlist
                    (user_chat_id, item_type, identifier, threshold_type, threshold_value, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (user_chat_id, item_type, identifier, threshold_type, threshold_value),
            )
            item_id = cursor.lastrowid
        conn.close()
        return item_id
    except sqlite3.Error as e:
        logger.error("Failed to insert watchlist item: %s", e)
        return -1


def deactivate_watchlist_item(
    watch_id: int,
    user_chat_id: int,
    db_path: str = DB_FILENAME,
) -> bool:
    """
    Mark a watchlist item as inactive (soft delete).

    Keeping the row means we retain alert history. The user_chat_id is included
    in the WHERE clause to prevent one user from deactivating another user's alert.

    Args:
        watch_id:     Row ID of the watchlist item.
        user_chat_id: Must match the item's owner.
        db_path:      Path to the database file.

    Returns:
        True if the update affected exactly one row, False otherwise.
    """
    try:
        conn = get_connection(db_path)
        with conn:
            cursor = conn.execute(
                "UPDATE watchlist SET active = 0 WHERE id = ? AND user_chat_id = ?",
                (watch_id, user_chat_id),
            )
            affected = cursor.rowcount
        conn.close()
        return affected == 1
    except sqlite3.Error as e:
        logger.error("Failed to deactivate watchlist item %d: %s", watch_id, e)
        return False


def get_active_watchlist(
    user_chat_id: int,
    db_path: str = DB_FILENAME,
) -> List[Dict[str, Any]]:
    """
    Retrieve all active watchlist items for a user.

    Args:
        user_chat_id: Telegram chat ID.
        db_path:      Path to the database file.

    Returns:
        List of watchlist item dicts with keys matching the table columns.
    """
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE user_chat_id = ? AND active = 1 ORDER BY id ASC",
            (user_chat_id,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error("Failed to query watchlist for user %d: %s", user_chat_id, e)
        return []
