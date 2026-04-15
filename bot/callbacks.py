"""
bot/callbacks.py
================
Inline button callback handlers for TGLP Bot — Sprint 11 fully wired.

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
"""

import logging
import math

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.strategy_manager import session_manager
from helpers.formatters import escape_md

logger = logging.getLogger(__name__)

# Matches _HISTORY_PAGE_SIZE in commands.py
_HISTORY_PAGE_SIZE = 5


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
    elif data.startswith("cs_"):
        # cs_confirm / cs_cancel are owned by the custom_strategy_handler
        # ConversationHandler; if they reach here the conversation has ended.
        await query.answer("Custom strategy setup is not active.", show_alert=True)
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
    the settings keyboard. Toggling pause also pauses/resumes the scheduler job.
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
        from core.scheduler import bot_scheduler
        session.paused = not session.paused
        if session.paused:
            bot_scheduler.pause_user_job(chat_id)
            await query.answer("Bot paused.", show_alert=False)
        else:
            bot_scheduler.resume_user_job(chat_id)
            await query.answer("Bot resumed.", show_alert=False)

    elif data == "cfg_change_strategy":
        from bot.keyboards import strategy_picker_keyboard
        await query.edit_message_text(
            "📋 *Change Strategy*\n\n"
            "Choose a new strategy profile\\. Your current position and "
            "watchlist will be kept\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=strategy_picker_keyboard(),
        )
        return  # Don't refresh the settings panel; we've replaced the message.

    elif data == "cfg_strat_cancel":
        # Return to the settings panel.
        from bot.keyboards import settings_menu_keyboard
        await query.edit_message_text(
            "⚙️ *Settings*\n\n"
            f"Strategy: {escape_md(session.active_strategy.name)}\n"
            f"Compounding: {'On' if session.compound_enabled else 'Off'}\n"
            f"Execution: {'Auto' if session.auto_execute else 'Confirm'}\n"
            f"Paused: {'Yes' if session.paused else 'No'}\n\n"
            "_Tap a button to change a setting\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=settings_menu_keyboard(
                compound_enabled=session.compound_enabled,
                auto_execute=session.auto_execute,
            ),
        )
        return

    elif data in ("cfg_strat_conservative", "cfg_strat_balanced", "cfg_strat_aggressive"):
        from config.settings import STRATEGY_PROFILES
        key_map = {
            "cfg_strat_conservative": "conservative",
            "cfg_strat_balanced":     "balanced",
            "cfg_strat_aggressive":   "aggressive",
        }
        strategy_key = key_map[data]
        new_strategy = STRATEGY_PROFILES.get(strategy_key)
        if new_strategy is None:
            await query.answer("Strategy not found.", show_alert=True)
            return
        session.active_strategy = new_strategy
        session.compound_enabled = new_strategy.compound_interval is not None
        session.auto_execute = new_strategy.auto_execute
        await query.answer(f"Strategy changed to {new_strategy.name}.", show_alert=False)
        # Fall through to refresh settings panel.

    elif data == "cfg_strat_custom":
        # Handled by the custom_strategy_handler ConversationHandler which is
        # registered before this catch-all. If we get here, direct the user.
        await query.answer()
        await query.edit_message_text(
            "🔧 Use /customstrategy to configure your custom strategy\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    elif data == "cfg_change_slippage":
        await query.answer(
            "Slippage is set by your strategy profile. Use 'Custom Strategy' to set a custom value.",
            show_alert=True,
        )
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
    """
    Pool detail view and back-navigation.

    Pool detail lookups use the snapshot stored in context.user_data by
    explore_command() (key: "explore_snapshot"), so they always reflect the
    data that was on screen when the user tapped the pool button.
    """
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    session = session_manager.get(chat_id)

    if data == "pool_back_list":
        # Re-render the explore list using the cached snapshot and scored list.
        snapshot = context.user_data.get("explore_snapshot")
        if snapshot is None or session is None:
            await query.edit_message_text(
                "🔍 *Pool Explorer*\n\n"
                "_No cached pool data\\. Run /explore to refresh\\._",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        from core.decision_engine import filter_pools_by_strategy, score_pools
        from helpers.formatters import format_pool_info
        from bot.keyboards import pool_list_keyboard

        filtered = filter_pools_by_strategy(snapshot.pools, session.active_strategy, None)
        scored = score_pools(filtered)

        page = 0
        page_size = 5
        total_pages = max(1, math.ceil(len(scored) / page_size))
        page_pools = scored[page * page_size: (page + 1) * page_size]

        lines = [
            f"🔍 *Pool Explorer*\n"
            f"Strategy: *{escape_md(session.active_strategy.name)}* — "
            f"{escape_md(str(len(scored)))} pools matched\n"
        ]
        for i, sp in enumerate(page_pools):
            pool_dict = {
                "pool":       sp.pool.pool,
                "symbol":     sp.pool.symbol,
                "apr":        sp.pool.apr,
                "tvl_usd":    sp.pool.tvl_usd,
                "volume_24h": sp.pool.volume_24h,
                "fee_tier":   sp.pool.fee_tier,
                "pair_type":  sp.pool.pair_type,
            }
            lines.append(format_pool_info(pool_dict, rank=i + 1))
            lines.append("")

        text = "\n".join(lines)
        pool_dicts = [{"symbol": sp.pool.symbol, "pool": sp.pool.pool} for sp in page_pools]
        keyboard = pool_list_keyboard(pool_dicts, page, total_pages)
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard
        )

    elif data.startswith("pool_detail_"):
        addr_fragment = data.replace("pool_detail_", "")
        snapshot = context.user_data.get("explore_snapshot")
        pool_data = None
        if snapshot:
            for p in snapshot.pools:
                if p.pool[:20] == addr_fragment or p.pool.startswith(addr_fragment):
                    pool_data = p
                    break

        if pool_data is None:
            await query.edit_message_text(
                f"📊 *Pool Details*\n\n"
                f"`{escape_md(addr_fragment)}\\.\\.\\. `\n\n"
                "_Pool not found in cached data\\. Run /explore to refresh\\._",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        from helpers.formatters import format_pool_info
        from bot.keyboards import pool_detail_keyboard

        pool_dict = {
            "pool":       pool_data.pool,
            "symbol":     pool_data.symbol,
            "apr":        pool_data.apr,
            "tvl_usd":    pool_data.tvl_usd,
            "volume_24h": pool_data.volume_24h,
            "fee_tier":   pool_data.fee_tier,
            "pair_type":  pool_data.pair_type,
        }
        text = f"📊 *Pool Details*\n\n{format_pool_info(pool_dict)}"
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=pool_detail_keyboard(pool_data.pool),
        )

    elif data.startswith("pool_page_"):
        page = int(data.replace("pool_page_", ""))
        snapshot = context.user_data.get("explore_snapshot")
        if snapshot is None or session is None:
            await query.edit_message_text(
                "🔍 *Pool Explorer*\n\n_Run /explore to refresh\\._",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        from core.decision_engine import filter_pools_by_strategy, score_pools
        from helpers.formatters import format_pool_info
        from bot.keyboards import pool_list_keyboard

        filtered = filter_pools_by_strategy(snapshot.pools, session.active_strategy, None)
        scored = score_pools(filtered)

        page_size = 5
        total_pages = max(1, math.ceil(len(scored) / page_size))
        page_pools = scored[page * page_size: (page + 1) * page_size]

        lines = [
            f"🔍 *Pool Explorer* — Page {page + 1}/{total_pages}\n"
            f"Strategy: *{escape_md(session.active_strategy.name)}*\n"
        ]
        for i, sp in enumerate(page_pools):
            pool_dict = {
                "pool":       sp.pool.pool,
                "symbol":     sp.pool.symbol,
                "apr":        sp.pool.apr,
                "tvl_usd":    sp.pool.tvl_usd,
                "volume_24h": sp.pool.volume_24h,
                "fee_tier":   sp.pool.fee_tier,
                "pair_type":  sp.pool.pair_type,
            }
            lines.append(format_pool_info(pool_dict, rank=page * page_size + i + 1))
            lines.append("")

        text = "\n".join(lines)
        pool_dicts = [{"symbol": sp.pool.symbol, "pool": sp.pool.pool} for sp in page_pools]
        keyboard = pool_list_keyboard(pool_dicts, page, total_pages)
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=keyboard
        )


# ---------------------------------------------------------------------------
# History pagination (hist_*)
# ---------------------------------------------------------------------------

async def _handle_history(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigate history pages and handle action-type filter buttons."""
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    session = session_manager.get(chat_id)

    if not session:
        await query.edit_message_text(
            "⚠️ Session not found\\. Run /start to set up\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Determine new filter and page from callback data.
    if data.startswith("hist_filter_"):
        new_filter = data.replace("hist_filter_", "")
        context.user_data["history_filter"] = new_filter
        page = 0
    elif data.startswith("hist_page_"):
        page = int(data.replace("hist_page_", ""))
        new_filter = context.user_data.get("history_filter", "all")
    else:
        return

    from helpers.database import get_trades_for_user, count_trades_for_user
    from helpers.formatters import format_tx_summary
    from bot.keyboards import history_filter_keyboard

    db_filter = None if new_filter == "all" else new_filter
    total = count_trades_for_user(session.chat_id, action_filter=db_filter)
    total_pages = max(1, math.ceil(total / _HISTORY_PAGE_SIZE))
    trades = get_trades_for_user(
        session.chat_id,
        limit=_HISTORY_PAGE_SIZE,
        offset=page * _HISTORY_PAGE_SIZE,
        action_filter=db_filter,
    )

    filter_label = f" \\| filter: *{escape_md(new_filter)}*" if new_filter != "all" else ""
    header = (
        f"📜 *Transaction History*{filter_label} — "
        f"Page {page + 1}/{total_pages} "
        f"\\({escape_md(str(total))} total\\)\n"
    )

    if not trades:
        lines = [
            header,
            "_No transactions match this filter\\._",
        ]
    else:
        lines = [header]
        for trade in trades:
            lines.append(format_tx_summary(trade))
            lines.append("")

    text = "\n".join(lines)
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=history_filter_keyboard(page, total_pages, new_filter),
    )


# ---------------------------------------------------------------------------
# Watchlist / alerts (alert_*, watch_*)
# ---------------------------------------------------------------------------

async def _handle_watchlist(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Watchlist alert removal and watch-add-from-explore callbacks."""
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data.startswith("alert_remove_"):
        # Parse the integer watch_id embedded in the callback data.
        try:
            watch_id = int(data.replace("alert_remove_", ""))
        except ValueError:
            await query.edit_message_text(
                "⚠️ Invalid alert ID\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        session = session_manager.get(chat_id)
        if not session:
            await query.edit_message_text(
                "⚠️ Session not found\\. Run /start to set up\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        from core.watchlist import remove_watch_item, load_watchlist
        removed = remove_watch_item(session, watch_id)

        if not removed:
            await query.answer("Alert not found or already removed.", show_alert=True)
            return

        # Refresh and re-render the alerts panel.
        load_watchlist(session)
        count = len(session.watchlist)

        if count == 0:
            await query.edit_message_text(
                "🔔 *Active Alerts*\n\n"
                "You have no active watchlist alerts\\.\n\n"
                "Use /watch to add a pool or token to monitor\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            from bot.keyboards import watchlist_keyboard
            await query.edit_message_text(
                f"🗑 Alert removed\\.\n\n"
                f"🔔 *Active Alerts* \\({escape_md(str(count))}\\)\n\n"
                f"Tap *Remove* next to an alert to delete it\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=watchlist_keyboard(session.watchlist),
            )

    elif data.startswith("watch_add_"):
        # "Add to watchlist" button from a pool detail view.
        # Redirect the user to the /watch conversation with the address pre-filled.
        addr_fragment = data.replace("watch_add_", "")
        await query.edit_message_text(
            f"👁 *Add to Watchlist*\n\n"
            f"Pool: `{escape_md(addr_fragment)}\\.\\.\\. `\n\n"
            f"Run /watch and enter this pool address to set an alert threshold\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Action confirmation (action_*)
# ---------------------------------------------------------------------------

async def _handle_action_confirm(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle user confirmation or skip for proposed LP actions.

    When auto_execute is False, the dispatcher sends a proposal via
    format_decision_summary() with an action_confirm_keyboard. The user
    confirms or skips here.

    Note: confirming re-runs the full cycle rather than caching and replaying
    the specific decision. This means a brief market update between the
    proposal and the confirmation might change the final action — which is
    the correct and safe behaviour for a live trading bot.
    """
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if data.startswith("action_confirm_"):
        session = session_manager.get(chat_id)
        if not session:
            await query.edit_message_text(
                "⚠️ Session not found\\. Run /start to set up\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if not session.is_operational():
            state = escape_md("paused") if session.paused else escape_md("safety-locked")
            await query.edit_message_text(
                f"⚠️ Bot is {state}\\. Cannot execute\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        w3 = context.application.bot_data.get("w3")
        notify_func = context.application.bot_data.get("notify_func")

        await query.edit_message_text(
            "⏳ Confirmed\\. Running execution cycle\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        import asyncio
        from core.dispatcher import run_cycle

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, run_cycle, session, notify_func, w3)
        except Exception as exc:
            logger.exception("action_confirm: cycle raised: %s", exc)

    elif data.startswith("action_skip_"):
        await query.edit_message_text(
            "⏭ Action skipped\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Reset confirmation (reset_*)
# ---------------------------------------------------------------------------

async def _handle_reset(query, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /reset confirmation keyboard.

    On confirm: removes the scheduler job, deletes the session, and sends
    a farewell message. On cancel: dismisses the keyboard.
    """
    await query.answer()
    chat_id = update.effective_chat.id

    if query.data == "reset_confirm_yes":
        session = session_manager.get(chat_id)
        if session:
            from core.scheduler import bot_scheduler
            bot_scheduler.remove_user_job(chat_id)
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
