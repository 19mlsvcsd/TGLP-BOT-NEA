"""
tests/test_sprint3.py
=====================
Sprint 3 verification tests.

Run with:
  python tests/test_sprint3.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TELEGRAM_BOT_TOKEN"] = "test_token_placeholder"


# ---------------------------------------------------------------------------
# Test 1: core/strategy_manager.py
# ---------------------------------------------------------------------------

def test_strategy_manager():
    from core.strategy_manager import (
        StrategyProfile, UserSession, SessionManager, session_manager
    )
    from config.settings import CONSERVATIVE_YIELD

    s = UserSession(
        chat_id=100,
        wallet_address="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
        private_key="a" * 64,
        active_strategy=CONSERVATIVE_YIELD,
        compound_enabled=True,
        auto_execute=False,
    )
    assert not s.has_position()
    assert s.is_operational()

    s.paused = True
    assert not s.is_operational()
    s.paused = False

    s.safety_locked = True
    assert not s.is_operational()
    s.safety_locked = False

    assert s.is_operational()

    # SessionManager CRUD
    sm = SessionManager()
    sm.create(s)
    assert sm.exists(100)
    assert sm.get(100).wallet_address == s.wallet_address
    assert sm.count() == 1
    assert sm.delete(100)
    assert not sm.exists(100)
    assert not sm.delete(100)   # double-delete returns False

    # Singleton identity
    from core.strategy_manager import session_manager as sm1
    import core.strategy_manager as csm
    assert sm1 is csm.session_manager, "session_manager is not a singleton"

    print("[PASS] core/strategy_manager.py")


# ---------------------------------------------------------------------------
# Test 2: helpers/formatters.py
# ---------------------------------------------------------------------------

def test_formatters():
    from helpers.formatters import (
        escape_md, format_bnb, format_usd, format_pct, format_large_usd,
        format_timedelta_short, short_address, format_strategy_summary,
    )
    from config.settings import CONSERVATIVE_YIELD

    # escape_md
    assert escape_md("hello_world") == r"hello\_world"
    assert escape_md("3.14") == r"3\.14"
    assert escape_md("(test)") == r"\(test\)"
    assert escape_md("a+b=c") == "a\\+b\\=c"
    assert escape_md("100%") == "100%"        # % is not a special char
    assert escape_md("a.b!c") == r"a\.b\!c"

    # Number formatters
    assert format_bnb(0.1234) == "0.1234 BNB"
    assert format_usd(1234.5) == "$1,234.50"
    assert format_pct(5.25) == "5.25%"
    assert format_large_usd(1_500_000) == "$1.50M"
    assert format_large_usd(500_000) == "$500.0K"
    assert format_large_usd(500) == "$500.00"
    assert format_large_usd(2_000_000_000) == "$2.00B"

    # Timedelta
    assert format_timedelta_short(45) == "45s"
    assert format_timedelta_short(1800) == "30 min"
    assert format_timedelta_short(3600) == "1 h"
    assert format_timedelta_short(86400) == "1 d"

    # Address helpers
    addr = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    short = short_address(addr)
    assert short.startswith("0xAb58"), f"Got: {short}"
    assert short.endswith("eC9B"), f"Got: {short}"
    assert "..." in short

    # format_strategy_summary: smoke test
    summary = format_strategy_summary(
        CONSERVATIVE_YIELD, True, False, addr, bnb_balance=0.5
    )
    assert isinstance(summary, str) and len(summary) > 50
    assert "Conservative" in summary
    assert "0xAb58" in summary
    # BNB value is escape_md'd so the dot becomes \., check the escaped form
    assert "BNB" in summary
    assert "0\\.5000" in summary   # escape_md turns "0.5000 BNB" → "0\.5000 BNB"

    print("[PASS] helpers/formatters.py")


# ---------------------------------------------------------------------------
# Test 3: bot/keyboards.py
# ---------------------------------------------------------------------------

def test_keyboards():
    from telegram import InlineKeyboardMarkup
    from bot.keyboards import (
        strategy_selection_keyboard, confirm_cancel_keyboard,
        settings_menu_keyboard, compound_toggle_keyboard,
        autoexecute_keyboard, custom_pairs_keyboard,
        history_keyboard, reset_confirm_keyboard,
        pool_detail_keyboard,
    )

    # Strategy selection: 4 rows
    kb = strategy_selection_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 4
    assert kb.inline_keyboard[0][0].callback_data == "ob_strat_conservative"
    assert kb.inline_keyboard[3][0].callback_data == "ob_strat_custom"

    # Settings keyboard: reflects compound ON
    sk = settings_menu_keyboard(compound_enabled=True, auto_execute=False)
    labels = [btn.text for row in sk.inline_keyboard for btn in row]
    assert any("ON" in l for l in labels), f"No ON label found: {labels}"
    assert any("Confirm" in l for l in labels), f"No Confirm label: {labels}"

    # Settings keyboard: reflects compound OFF
    sk2 = settings_menu_keyboard(compound_enabled=False, auto_execute=True)
    labels2 = [btn.text for row in sk2.inline_keyboard for btn in row]
    assert any("OFF" in l for l in labels2)
    assert any("Auto" in l for l in labels2)

    # Confirm/cancel keyboard
    ck = confirm_cancel_keyboard()
    assert ck.inline_keyboard[0][0].callback_data == "ob_final_confirm"
    assert ck.inline_keyboard[0][1].callback_data == "ob_final_cancel"

    # History pagination: 1 page → no nav buttons
    assert len(history_keyboard(0, 1).inline_keyboard) == 0
    # Multi-page → has prev/next
    nav = history_keyboard(1, 5)
    nav_data = [btn.callback_data for row in nav.inline_keyboard for btn in row]
    assert "hist_page_0" in nav_data  # Prev
    assert "hist_page_2" in nav_data  # Next

    # Reset confirmation
    rk = reset_confirm_keyboard()
    assert rk.inline_keyboard[0][0].callback_data == "reset_confirm_yes"
    assert rk.inline_keyboard[0][1].callback_data == "reset_confirm_no"

    print("[PASS] bot/keyboards.py")


# ---------------------------------------------------------------------------
# Test 4: all bot modules import cleanly
# ---------------------------------------------------------------------------

def test_imports():
    import bot.onboarding     # noqa
    import bot.commands       # noqa
    import bot.callbacks      # noqa
    import bot.conversations  # noqa
    import bot.app            # noqa

    from telegram.ext import ConversationHandler
    from bot.onboarding import (
        onboarding_handler, AWAITING_KEY, AWAITING_STRATEGY, AWAITING_CONFIRM
    )
    assert isinstance(onboarding_handler, ConversationHandler)
    # Ten states numbered 0–9
    assert AWAITING_KEY == 0
    assert AWAITING_CONFIRM == 9
    # Correct number of states registered
    assert len(onboarding_handler.states) == 10

    print("[PASS] all bot modules import cleanly")
    print("[PASS] onboarding ConversationHandler structure")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_strategy_manager()
    test_formatters()
    test_keyboards()
    test_imports()
    print()
    print("All Sprint 3 tests passed.")
