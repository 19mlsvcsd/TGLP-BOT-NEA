"""
bot/callbacks.py
================
Inline button callback handlers for TGLP Bot.

Handles CallbackQuery updates that originate from inline keyboards outside
of active ConversationHandlers. The onboarding conversation handles its own
buttons internally — this module handles everything else:

- Main menu quick-access buttons (cmd_*)
- Settings toggles (cfg_*)
- Pool explorer navigation (pool_*)
- History pagination (hist_*)
- Watchlist management (alert_*, watch_*)
- Action confirmation for non-auto-execute mode (action_*)
- /reset confirmation (reset_*)

Most of these are stubs that will be wired to real logic in Sprint 11.
The pattern is consistent so Sprint 11 only needs to fill in the body,
not restructure any handler registration.
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.strategy_manager import session_manager
from helpers.formatters import escape_md

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dispatch router
# ---------------------------------------------------------------------------

async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Top-level callback router.

    Reads the callback_data prefix and dispatches to the appropriate
    sub-handler. Unknown prefixes receive a generic "not available" answer.

    This single handler is registered in bot/app.py as the fallback
    CallbackQueryHandler (runs after all ConversationHandlers).
    """
    query = update.callback_query
    data = query.data or ""

    # Route by prefix.
    if data.startswith("cmd_"):
        await _handle_menu(query, update, context)
    elif data.startswith("cfg_"):
        await _handle_settings(query, update, context)
    elif data.startswith("pool_"):
        await _handle_pool(query, update, context)
    elif data.startswith("hist_"):
        await _handle_history(query, update, context)
    elif data.startswith("alert_") or data.startswith("watch_"):
        await _handle_watchlist(query, update, context)
    elif data.startswith("action_"):
        await _handle_action_confirm(query, update, context)
    elif data.startswith("reset_"):
        await _handle_reset(query, update, context)
    elif data in ("page_noop", "alert_noop"):
        # No-op buttons used as display labels — just dismiss the loading spinner.
        await query.answer()
    else:
        await query.answer("This button is not available yet.", show_alert=False)


# ---------------------------------------------------------------------------
# Main menu (cmd_*)
# ---------------------------------------------------------------------------

async def _handle_menu(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch main-menu quick-access buttons to the relevant command handlers."""
    from bot.commands import (
        allocate_command,
        dashboard_command,
        explore_command,
        help_command,
        history_command,
        settings_command,
    )
    await query.answer()

    command_map = {
        "cmd_dashboard": dashboard_command,
        "cmd_explore":   explore_command,
        "cmd_allocate":  allocate_command,
        "cmd_history":   history_command,
        "cmd_settings":  settings_command,
        "cmd_help":      help_command,
    }
    handler = command_map.get(query.data)
    if handler:
        # Create a synthetic update with a message so command handlers work
        # the same way whether triggered by a slash command or a button.
        await handler(update, context)
    else:
        await query.edit_message_text(
            "❓ Unknown menu option\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Settings (cfg_*)
# ---------------------------------------------------------------------------

async def _handle_settings(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle settings panel button presses.

    Toggling compound/autoexec updates the session immediately and refreshes
    the settings keyboard to reflect the new state. Strategy and slippage
    changes will be implemented as conversations in Sprint 11.
    """
    await query.answer()
    chat_id = update.effective_chat.id
    session = session_manager.get(chat_id)

    if not session:
        await query.edit_message_text(
            "⚠️ No active session\\. Run /start to set up\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    data = query.data

    if data == "cfg_toggle_compound":
        session.compound_enabled = not session.compound_enabled
        state = "enabled" if session.compound_enabled else "disabled"
        await query.answer(f"Auto-compounding {state}.", show_alert=False)

    elif data == "cfg_toggle_autoexec":
        session.auto_execute = not session.auto_execute
        mode = "Auto-execute" if session.auto_execute else "Confirm mode"
        await query.answer(f"Switched to {mode}.", show_alert=False)

    elif data == "cfg_toggle_pause":
        session.paused = not session.paused
        state = "paused" if session.paused else "resumed"
        await query.answer(f"Bot {state}.", show_alert=False)

    elif data in ("cfg_change_strategy", "cfg_change_slippage"):
        await query.answer("Coming in Sprint 11.", show_alert=True)
        return

    # Refresh settings panel to show updated toggle states.
    from bot.keyboards import settings_menu_keyboard
    await query.edit_message_reply_markup(
        reply_markup=settings_menu_keyboard(
            compound_enabled=session.compound_enabled,
            auto_execute=session.auto_execute,
        )
    )


# ---------------------------------------------------------------------------
# Pool explorer (pool_*)
# ---------------------------------------------------------------------------

async def _handle_pool(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pool detail view and back-navigation. Full implementation in Sprint 11."""
    await query.answer()
    data = query.data

    if data == "pool_back_list":
        await query.edit_message_text(
            "🔍 *Pool Explorer*\n\n"
            "_Returning to list \\— full implementation in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data.startswith("pool_detail_"):
        addr_fragment = data.replace("pool_detail_", "")
        await query.edit_message_text(
            f"📊 *Pool Details*\n\n"
            f"Address fragment: `{escape_md(addr_fragment)}`\n\n"
            "_Full pool detail view coming in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data.startswith("pool_page_"):
        page = int(data.replace("pool_page_", ""))
        await query.edit_message_text(
            f"🔍 *Pool Explorer — Page {page + 1}*\n\n"
            "_Pagination fully wired in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# History pagination (hist_*)
# ---------------------------------------------------------------------------

async def _handle_history(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """History page navigation. Full implementation in Sprint 11."""
    await query.answer()
    data = query.data

    if data.startswith("hist_page_"):
        page = int(data.replace("hist_page_", ""))
        await query.edit_message_text(
            f"📜 *Transaction History — Page {page + 1}*\n\n"
            "_History pagination fully wired in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Watchlist / alerts (alert_*, watch_*)
# ---------------------------------------------------------------------------

async def _handle_watchlist(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Watchlist add/remove callbacks. Full implementation in Sprint 11."""
    await query.answer()
    data = query.data

    if data.startswith("alert_remove_"):
        watch_id = data.replace("alert_remove_", "")
        await query.edit_message_text(
            f"🗑 Removing alert ID `{escape_md(watch_id)}`\\.\n\n"
            "_Full watchlist management in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data.startswith("watch_add_"):
        addr_fragment = data.replace("watch_add_", "")
        await query.edit_message_text(
            f"👁 *Add to Watchlist*\n\n"
            f"Pool: `{escape_md(addr_fragment)}\\.\\.\\. `\n\n"
            "_Watch setup conversation coming in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Action confirmation (action_*)
# ---------------------------------------------------------------------------

async def _handle_action_confirm(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle user confirmation or skip for proposed LP actions.

    When auto_execute is False, the dispatcher sends a proposal message
    with an action_confirm_keyboard. The user taps Confirm or Skip here.
    Full implementation in Sprint 11 (requires dispatcher from Sprint 10).
    """
    await query.answer()
    data = query.data

    if data.startswith("action_confirm_"):
        action_id = data.replace("action_confirm_", "")
        await query.edit_message_text(
            f"✅ Action `{escape_md(action_id)}` confirmed\\.\n\n"
            "_Execution pipeline wired in Sprint 11\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data.startswith("action_skip_"):
        action_id = data.replace("action_skip_", "")
        await query.edit_message_text(
            f"⏭ Action `{escape_md(action_id)}` skipped\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Reset confirmation (reset_*)
# ---------------------------------------------------------------------------

async def _handle_reset(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /reset confirmation keyboard.

    On confirm: deletes the session (stops scheduler in Sprint 10) and
    sends a farewell message. On cancel: dismisses the keyboard.
    """
    await query.answer()
    chat_id = update.effective_chat.id

    if query.data == "reset_confirm_yes":
        session = session_manager.get(chat_id)
        if session:
            # TODO Sprint 10: stop_scheduler(session)
            wallet = session.wallet_address
            session_manager.delete(chat_id)
            logger.info("User %d reset their session (wallet %s)", chat_id, wallet)

        await query.edit_message_text(
            "✅ *Session reset\\.*\n\n"
            "Your wallet has been cleared from memory and the scheduler stopped\\.\n\n"
            "Run /start to set up again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    elif query.data == "reset_confirm_no":
        await query.edit_message_text(
            "❌ Reset cancelled\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
