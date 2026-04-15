"""
tests/test_sprint11.py
======================
Sprint 11 verification tests for the wired-up bot layer.

Tests are unit-only (no network, no Telegram API calls).

Run with:
  python tests/test_sprint11.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(chat_id=70001, paused=False, safety_locked=False, auto_execute=False):
    from core.strategy_manager import UserSession
    from config.settings import BALANCED_GROWTH
    return UserSession(
        chat_id=chat_id,
        wallet_address="0x0000000000000000000000000000000000000001",
        private_key="0x" + "a" * 64,
        active_strategy=BALANCED_GROWTH,
        compound_enabled=False,
        auto_execute=auto_execute,
        paused=paused,
        safety_locked=safety_locked,
    )


# ===========================================================================
# Module import checks
# ===========================================================================

def test_bot_modules_import():
    """All bot modules must import without raising."""
    import bot.app          # noqa: F401
    import bot.commands     # noqa: F401
    import bot.callbacks    # noqa: F401
    import bot.conversations  # noqa: F401
    import bot.keyboards    # noqa: F401
    import bot.onboarding   # noqa: F401
    print("[PASS] All bot modules import cleanly")


# ===========================================================================
# bot/app.py
# ===========================================================================

def test_app_has_lifecycle_hooks():
    """_post_init and _post_shutdown must be callable async functions."""
    import inspect
    from bot.app import _post_init, _post_shutdown
    assert inspect.iscoroutinefunction(_post_init), "_post_init must be async"
    assert inspect.iscoroutinefunction(_post_shutdown), "_post_shutdown must be async"
    print("[PASS] bot/app.py: _post_init and _post_shutdown are async")


# ===========================================================================
# bot/conversations.py: /watch flow
# ===========================================================================

def test_watch_handler_state_count():
    """The /watch ConversationHandler must have exactly 3 states."""
    from bot.conversations import watch_conversation_handler
    assert len(watch_conversation_handler.states) == 3, (
        f"Expected 3 states, got {len(watch_conversation_handler.states)}"
    )
    print("[PASS] watch_conversation_handler has 3 states")


def test_watch_handler_state_keys():
    """The /watch ConversationHandler state keys must be 0, 1, 2."""
    from bot.conversations import (
        watch_conversation_handler,
        WATCH_AWAITING_IDENTIFIER,
        WATCH_AWAITING_THRESHOLD_TYPE,
        WATCH_AWAITING_THRESHOLD_VALUE,
    )
    keys = set(watch_conversation_handler.states.keys())
    expected = {
        WATCH_AWAITING_IDENTIFIER,
        WATCH_AWAITING_THRESHOLD_TYPE,
        WATCH_AWAITING_THRESHOLD_VALUE,
    }
    assert keys == expected, f"State keys mismatch: {keys} vs {expected}"
    print("[PASS] watch_conversation_handler has correct state keys (0, 1, 2)")


def test_watch_threshold_type_keyboard():
    """_threshold_type_keyboard() must return 3 buttons with wt_ callback prefixes."""
    from bot.conversations import _threshold_type_keyboard
    kb = _threshold_type_keyboard()
    # InlineKeyboardMarkup.inline_keyboard is a tuple of rows.
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 3, f"Expected 3 threshold buttons, got {len(buttons)}"
    for btn in buttons:
        assert btn.callback_data.startswith("wt_"), (
            f"Button callback_data should start with 'wt_': {btn.callback_data}"
        )
    print("[PASS] _threshold_type_keyboard(): 3 buttons with wt_ prefixes")


# ===========================================================================
# bot/keyboards.py
# ===========================================================================

def test_watchlist_keyboard_empty():
    """watchlist_keyboard([]) must return a keyboard with a no-alerts label button."""
    from bot.keyboards import watchlist_keyboard
    kb = watchlist_keyboard([])
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert buttons[0].callback_data == "alert_noop"
    print("[PASS] watchlist_keyboard([]): single no-alerts label button")


def test_watchlist_keyboard_with_items():
    """watchlist_keyboard must produce one Remove button per watchlist item."""
    from bot.keyboards import watchlist_keyboard
    items = [
        {"id": 1, "identifier": "0xABC", "threshold_type": "apr_above"},
        {"id": 2, "identifier": "WBNB",  "threshold_type": "apr_below"},
    ]
    kb = watchlist_keyboard(items)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 2
    assert buttons[0].callback_data == "alert_remove_1"
    assert buttons[1].callback_data == "alert_remove_2"
    print("[PASS] watchlist_keyboard with items: one Remove button per item")


def test_pool_list_keyboard_structure():
    """pool_list_keyboard must produce pool buttons plus navigation row."""
    from bot.keyboards import pool_list_keyboard
    pools = [
        {"symbol": "USDT-WBNB", "pool": "0x" + "a" * 40},
        {"symbol": "CAKE-BNB",  "pool": "0x" + "b" * 40},
    ]
    # Single page, no nav row.
    kb = pool_list_keyboard(pools, page=0, total_pages=1)
    rows = kb.inline_keyboard
    # 2 pool buttons, no pagination row.
    assert len(rows) == 2, f"Expected 2 rows (no pagination), got {len(rows)}"
    assert rows[0][0].callback_data.startswith("pool_detail_")
    print("[PASS] pool_list_keyboard: 2 pool rows, no nav for single page")


# ===========================================================================
# bot/commands.py: page calculation
# ===========================================================================

def test_history_page_calculation():
    """Page count must ceil(total / page_size)."""
    import math
    from bot.commands import _HISTORY_PAGE_SIZE
    test_cases = [
        (0,  1),   # zero trades → 1 (empty) page
        (1,  1),
        (5,  1),
        (6,  2),
        (10, 2),
        (11, 3),
    ]
    for total, expected_pages in test_cases:
        pages = max(1, math.ceil(total / _HISTORY_PAGE_SIZE))
        assert pages == expected_pages, (
            f"total={total}: expected {expected_pages} pages, got {pages}"
        )
    print("[PASS] History page calculation is correct for all edge cases")


def test_explore_page_size_defined():
    """_EXPLORE_PAGE_SIZE must be a positive integer."""
    from bot.commands import _EXPLORE_PAGE_SIZE
    assert isinstance(_EXPLORE_PAGE_SIZE, int) and _EXPLORE_PAGE_SIZE > 0
    print(f"[PASS] _EXPLORE_PAGE_SIZE = {_EXPLORE_PAGE_SIZE}")


# ===========================================================================
# helpers/formatters.py: used heavily in Sprint 11
# ===========================================================================

def test_format_usd():
    """format_usd must produce correctly formatted strings."""
    from helpers.formatters import format_usd
    assert format_usd(0.0)       == "$0.00"
    assert format_usd(1234.56)   == "$1,234.56"
    assert format_usd(1_000_000) == "$1,000,000.00"
    print("[PASS] format_usd: correct output for 0, 1234.56, 1M")


def test_format_bnb():
    """format_bnb must produce 4-decimal output with BNB suffix."""
    from helpers.formatters import format_bnb
    assert format_bnb(0.0)      == "0.0000 BNB"
    assert format_bnb(1.23456)  == "1.2346 BNB"
    print("[PASS] format_bnb: 4 decimal places, BNB suffix")


def test_format_pct():
    """format_pct must produce a percentage string."""
    from helpers.formatters import format_pct
    assert format_pct(5.25)  == "5.25%"
    assert format_pct(0.0)   == "0.00%"
    assert format_pct(100.0) == "100.00%"
    print("[PASS] format_pct: correct percentage strings")


def test_format_large_usd():
    """format_large_usd must use K/M/B suffixes."""
    from helpers.formatters import format_large_usd
    assert format_large_usd(500)         == "$500.00"
    assert format_large_usd(1_500)       == "$1.5K"
    assert format_large_usd(2_000_000)   == "$2.00M"
    assert format_large_usd(3_000_000_000) == "$3.00B"
    print("[PASS] format_large_usd: K/M/B suffixes correct")


def test_escape_md_special_chars():
    """escape_md must escape all MarkdownV2 special characters."""
    from helpers.formatters import escape_md
    raw = "hello_world.test(foo)[bar]"
    escaped = escape_md(raw)
    # Each special char should be prefixed by a backslash.
    assert r"\." in escaped
    assert r"\_" in escaped
    assert r"\(" in escaped
    assert r"\[" in escaped
    print("[PASS] escape_md: special chars correctly escaped")


# ===========================================================================
# core/watchlist.py: used by /alerts and /watch
# ===========================================================================

def test_watchlist_add_and_remove():
    """add_watch_item and remove_watch_item must work with an isolated DB."""
    import tempfile, os
    from core.watchlist import add_watch_item, remove_watch_item, load_watchlist

    # Use a temporary DB so this test doesn't touch the live database.
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    try:
        from helpers.database import initialise_database
        initialise_database(db_path=db_path)

        session = _make_session(chat_id=70010)
        session.watchlist = []

        # Monkeypatch the DB path for these calls.
        import core.watchlist as wl_module
        import helpers.database as db_module
        original_db = db_module.DB_FILENAME

        # Redirect all DB calls to our temp file.
        db_module.DB_FILENAME = db_path

        watch_id = add_watch_item(
            session,
            item_type="pool",
            identifier="0xPoolABC",
            threshold_type="apr_below",
            threshold_value=5.0,
        )
        assert watch_id > 0, "add_watch_item should return a positive ID"
        assert len(session.watchlist) == 1

        removed = remove_watch_item(session, watch_id)
        assert removed is True
        assert len(session.watchlist) == 0

        db_module.DB_FILENAME = original_db
    finally:
        os.unlink(db_path)

    print("[PASS] add_watch_item / remove_watch_item: add, verify, remove")


# ===========================================================================
# Integration: session and scheduler interaction
# ===========================================================================

def test_session_is_operational_after_pause():
    """A paused session must not be operational; unpausing must restore it."""
    session = _make_session(paused=False)
    assert session.is_operational() is True

    session.paused = True
    assert session.is_operational() is False

    session.paused = False
    assert session.is_operational() is True
    print("[PASS] session.is_operational() responds correctly to pause toggle")


def test_session_is_operational_safety_lock():
    """A safety-locked session must not be operational."""
    session = _make_session(safety_locked=True)
    assert session.is_operational() is False
    print("[PASS] session.is_operational() is False when safety_locked=True")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # Module imports
    test_bot_modules_import()

    # bot/app.py
    test_app_has_lifecycle_hooks()

    # bot/conversations.py
    test_watch_handler_state_count()
    test_watch_handler_state_keys()
    test_watch_threshold_type_keyboard()

    # bot/keyboards.py
    test_watchlist_keyboard_empty()
    test_watchlist_keyboard_with_items()
    test_pool_list_keyboard_structure()

    # bot/commands.py
    test_history_page_calculation()
    test_explore_page_size_defined()

    # helpers/formatters.py
    test_format_usd()
    test_format_bnb()
    test_format_pct()
    test_format_large_usd()
    test_escape_md_special_chars()

    # core/watchlist.py
    test_watchlist_add_and_remove()

    # Session integration
    test_session_is_operational_after_pause()
    test_session_is_operational_safety_lock()

    print()
    print("All Sprint 11 tests passed.")
