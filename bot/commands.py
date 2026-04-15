"""
bot/commands.py
===============
Command handlers for TGLP Bot — Sprint 11 full implementations.

Every Telegram slash command has its handler function here. All commands
that were stubs in earlier sprints are now fully wired to their core modules.

Handler registration happens in bot/app.py. Command functions here are kept
thin: they validate the user has a session, then delegate to core modules
(portfolio, market_data, decision_engine, etc.).

w3 and notify_func are retrieved from context.application.bot_data, where
they are placed by bot/app.py's _post_init hook.
"""

import asyncio
import logging
import math

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.strategy_manager import session_manager
from helpers.formatters import escape_md

logger = logging.getLogger(__name__)

# Number of trades to show per /history page.
_HISTORY_PAGE_SIZE = 5

# Number of pools to show per /explore page.
_EXPLORE_PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# Helper: require an active session
# ---------------------------------------------------------------------------

async def _require_session(update: Update) -> bool:
    """
    Check that the user has completed onboarding.

    If no session exists, sends a prompt to run /start and returns False.
    Command handlers call this at the top and return immediately if False.

    Args:
        update: The incoming Update object.

    Returns:
        True if a session exists for this user, False otherwise.
    """
    chat_id = update.effective_chat.id
    if not session_manager.exists(chat_id):
        await update.message.reply_text(
            "👋 You haven't set up your wallet yet\\. "
            "Run /start to get started\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# /dashboard
# ---------------------------------------------------------------------------

async def dashboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /dashboard — portfolio overview: position, P&L, gas costs, system health.
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)
    w3 = context.application.bot_data.get("w3")

    from core.portfolio import build_portfolio_summary
    from core.safety import safety_controller
    from helpers.formatters import (
        format_bnb, format_usd, format_pct,
        format_token_amount, short_address,
    )

    # Fetch BNB price from the cached market snapshot.
    bnb_price = 0.0
    try:
        from core.market_data import get_market_snapshot
        loop = asyncio.get_running_loop()
        snapshot = await loop.run_in_executor(
            None, lambda: get_market_snapshot(w3=w3)
        )
        bnb_price = snapshot.prices.get("BNB", 0.0)
    except Exception as exc:
        logger.warning("dashboard: could not fetch BNB price: %s", exc)

    # build_portfolio_summary makes one live RPC call — run in thread pool.
    loop = asyncio.get_running_loop()
    summary = await loop.run_in_executor(
        None, build_portfolio_summary, w3, session, bnb_price
    )

    # ── Status line ────────────────────────────────────────────────────────
    if session.safety_locked:
        status_str = "🔒 Safety locked"
    elif session.paused:
        status_str = "⏸ Paused"
    else:
        status_str = "✅ Active"

    # ── Position block ─────────────────────────────────────────────────────
    if summary.has_position and summary.position_value:
        pv = summary.position_value
        pos_lines = (
            f"*Position \\(open\\):*\n"
            f"  `{escape_md(format_token_amount(pv.amount0, pv.token0_symbol))}` "
            f"\\+ `{escape_md(format_token_amount(pv.amount1, pv.token1_symbol))}`\n"
            f"  Est\\. value: `{escape_md(format_usd(pv.value_usd))}`"
        )
    else:
        pos_lines = "*Position:* None"

    # ── P&L block ──────────────────────────────────────────────────────────
    pnl = summary.pnl
    pnl_icon = "📈" if pnl.unrealised_pnl_usd >= 0 else "📉"
    pnl_lines = (
        f"*P&L:*\n"
        f"  Entry: `{escape_md(format_usd(pnl.entry_value_usd))}`\n"
        f"  Current: `{escape_md(format_usd(pnl.current_value_usd))}`\n"
        f"  {pnl_icon} Unrealised: `{escape_md(format_usd(pnl.unrealised_pnl_usd))}` "
        f"\\({escape_md(format_pct(pnl.unrealised_pnl_pct))}\\)\n"
        f"  Gas spent: `{escape_md(format_bnb(pnl.gas_spent_bnb))}` "
        f"\\(`{escape_md(format_usd(pnl.gas_cost_usd))}`\\)\n"
        f"  Net P&L: `{escape_md(format_usd(pnl.net_pnl_usd))}`\n"
        f"  Rebalances: {pnl.rebalance_count}"
    )

    # ── System health block ────────────────────────────────────────────────
    try:
        health = safety_controller.get_system_health(w3)
        latency_ms = health.get("rpc_latency_ms", 0)
        gas_gwei = health.get("gas_price_gwei", 0.0)
        health_str = (
            f"RPC latency: `{escape_md(f'{latency_ms:.0f}')}ms` \\| "
            f"Gas: `{escape_md(f'{gas_gwei:.2f}')} Gwei`"
        )
    except Exception:
        health_str = "System health: N/A"

    text = (
        f"📊 *Dashboard*\n\n"
        f"Wallet: `{escape_md(short_address(session.wallet_address))}`\n"
        f"Strategy: {escape_md(session.active_strategy.name)}\n"
        f"Status: {escape_md(status_str)}\n"
        f"Wallet balance: `{escape_md(format_bnb(summary.wallet_bnb))}` "
        f"\\(`{escape_md(format_usd(summary.wallet_usd))}`\\)\n\n"
        f"{pos_lines}\n\n"
        f"{pnl_lines}\n\n"
        f"*System:* {health_str}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


# ---------------------------------------------------------------------------
# /allocate
# ---------------------------------------------------------------------------

async def allocate_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /allocate — manually trigger an immediate analysis and allocation cycle.

    Runs the full dispatcher pipeline (market snapshot → analysis → decision
    → execute/propose) in a thread-pool executor so the event loop is not
    blocked during RPC calls.
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)

    if not session.is_operational():
        state = escape_md("paused") if session.paused else escape_md("safety-locked")
        await update.message.reply_text(
            f"⚠️ Bot is {state}\\. Cannot run a cycle\\.\n\n"
            f"Use /settings to resume or clear the safety lock\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    w3 = context.application.bot_data.get("w3")
    notify_func = context.application.bot_data.get("notify_func")

    if w3 is None:
        await update.message.reply_text(
            "⚠️ Blockchain connection not available\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.message.reply_text(
        "⏳ Running analysis cycle\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    from core.dispatcher import run_cycle

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, run_cycle, session, notify_func, w3)
        await update.message.reply_text(
            "✅ Cycle complete\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as exc:
        logger.exception("allocate_command: cycle raised: %s", exc)
        await update.message.reply_text(
            f"❌ Cycle error: `{escape_md(str(exc))}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# /explore
# ---------------------------------------------------------------------------

async def explore_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /explore — browse top pools filtered and scored by the user's strategy.

    Shows _EXPLORE_PAGE_SIZE pools per page with a pagination keyboard.
    Pool detail buttons store the address fragment in callback_data so
    callbacks.py can look up the full pool object from the snapshot stored
    in context.user_data["explore_snapshot"].
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)
    w3 = context.application.bot_data.get("w3")

    await update.message.reply_text(
        "⏳ Fetching pool data\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    from core.market_data import get_market_snapshot
    from core.decision_engine import filter_pools_by_strategy, score_pools
    from helpers.formatters import format_pool_info
    from bot.keyboards import pool_list_keyboard

    loop = asyncio.get_running_loop()
    try:
        snapshot = await loop.run_in_executor(
            None, lambda: get_market_snapshot(w3=w3)
        )
    except Exception as exc:
        logger.exception("explore_command: snapshot error: %s", exc)
        await update.message.reply_text(
            f"❌ Could not fetch pool data: `{escape_md(str(exc))}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if not snapshot.pools:
        await update.message.reply_text(
            "⚠️ No pool data available right now\\. Try again shortly\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Store the snapshot so pool detail callbacks can look up full objects.
    context.user_data["explore_snapshot"] = snapshot

    filtered = filter_pools_by_strategy(snapshot.pools, session.active_strategy, None)
    scored = score_pools(filtered)

    if not scored:
        await update.message.reply_text(
            f"🔍 *Pool Explorer*\n\n"
            f"No pools matched the *{escape_md(session.active_strategy.name)}* "
            f"strategy filters\\.\n\n"
            f"Try /settings to change your strategy\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await _send_explore_page(update, context, scored, session, page=0)


async def _send_explore_page(update, context, scored, session, page: int) -> None:
    """Render one page of explore results and send it."""
    from helpers.formatters import format_pool_info
    from bot.keyboards import pool_list_keyboard

    page_size = _EXPLORE_PAGE_SIZE
    total_pages = max(1, math.ceil(len(scored) / page_size))
    page_pools = scored[page * page_size: (page + 1) * page_size]

    lines = [
        f"🔍 *Pool Explorer*\n"
        f"Strategy: *{escape_md(session.active_strategy.name)}* — "
        f"{escape_md(str(len(scored)))} pools matched\n"
    ]
    for i, sp in enumerate(page_pools):
        pool_dict = {
            "pool":      sp.pool.pool,
            "symbol":    sp.pool.symbol,
            "apr":       sp.pool.apr,
            "tvl_usd":   sp.pool.tvl_usd,
            "volume_24h":sp.pool.volume_24h,
            "fee_tier":  sp.pool.fee_tier,
            "pair_type": sp.pool.pair_type,
        }
        rank = page * page_size + i + 1
        lines.append(format_pool_info(pool_dict, rank=rank))
        lines.append("")

    text = "\n".join(lines)

    pool_dicts_for_keyboard = [
        {"symbol": sp.pool.symbol, "pool": sp.pool.pool} for sp in page_pools
    ]
    keyboard = pool_list_keyboard(pool_dicts_for_keyboard, page, total_pages)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# /watch — entry point (conversation is in bot/conversations.py)
# ---------------------------------------------------------------------------

async def watch_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /watch — redirects into the /watch ConversationHandler.

    The ConversationHandler in bot/conversations.py registers its own
    entry_point CommandHandler for /watch, so this standalone handler is
    only reached if the conversation is not active.  It simply informs the
    user to use /watch again to start the flow.
    """
    if not await _require_session(update):
        return

    await update.message.reply_text(
        "👁 *Add to Watchlist*\n\n"
        "Use /watch again to start monitoring a pool address or token\\.\n\n"
        "Supported thresholds:\n"
        "• APR above X% — alert when yield rises above your target\n"
        "• APR below X% — alert when yield drops too low\n"
        "• TVL below $X — alert when pool liquidity falls\n\n"
        "Type /cancel at any time to exit the setup\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /alerts
# ---------------------------------------------------------------------------

async def alerts_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /alerts — view and manage active watchlist alerts.
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)

    from core.watchlist import load_watchlist
    from bot.keyboards import watchlist_keyboard

    # Refresh from DB so the list is always current.
    load_watchlist(session)

    count = len(session.watchlist)
    if count == 0:
        await update.message.reply_text(
            "🔔 *Active Alerts*\n\n"
            "You have no active watchlist alerts\\.\n\n"
            "Use /watch to add a pool or token to monitor\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.message.reply_text(
        f"🔔 *Active Alerts* \\({escape_md(str(count))}\\)\n\n"
        f"Tap *Remove* next to an alert to delete it\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=watchlist_keyboard(session.watchlist),
    )


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def history_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /history — paginated list of all transactions with action-type filter.
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)
    # Clear any stale filter when /history is called fresh.
    context.user_data.pop("history_filter", None)
    await _send_history_page(update, session, page=0, context=context)


async def _send_history_page(
    update,
    session,
    page: int,
    context=None,
    action_filter: str = "all",
) -> None:
    """
    Render one page of trade history and send it.

    Args:
        update:        Telegram Update object.
        session:       UserSession for the requesting user.
        page:          0-based page index.
        context:       ContextTypes.DEFAULT_TYPE — read history_filter from
                       user_data if present (overrides action_filter arg).
        action_filter: Active filter key; 'all' means no filter.
    """
    from helpers.database import get_trades_for_user, count_trades_for_user
    from helpers.formatters import format_tx_summary
    from bot.keyboards import history_filter_keyboard

    # Prefer the stored filter in context over the parameter.
    if context is not None:
        action_filter = context.user_data.get("history_filter", action_filter)

    db_filter = None if action_filter == "all" else action_filter

    total = count_trades_for_user(session.chat_id, action_filter=db_filter)
    total_pages = max(1, math.ceil(total / _HISTORY_PAGE_SIZE))
    trades = get_trades_for_user(
        session.chat_id,
        limit=_HISTORY_PAGE_SIZE,
        offset=page * _HISTORY_PAGE_SIZE,
        action_filter=db_filter,
    )

    if not trades and page == 0:
        filter_note = (
            f" \\(filter: *{escape_md(action_filter)}*\\)" if action_filter != "all" else ""
        )
        await update.message.reply_text(
            f"📜 *Transaction History*{filter_note}\n\n"
            "No transactions recorded yet\\.\n\n"
            "_Transactions will appear here after your first allocation\\._",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=history_filter_keyboard(0, 1, action_filter),
        )
        return

    filter_label = f" \\| filter: *{escape_md(action_filter)}*" if action_filter != "all" else ""
    header = (
        f"📜 *Transaction History*{filter_label} — "
        f"Page {page + 1}/{total_pages} "
        f"\\({escape_md(str(total))} total\\)\n"
    )
    lines = [header]
    for trade in trades:
        lines.append(format_tx_summary(trade))
        lines.append("")

    text = "\n".join(lines)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=history_filter_keyboard(page, total_pages, action_filter),
    )


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------

async def export_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /export — send the complete trade history as formatted text.

    Splits into multiple messages if the history exceeds Telegram's 4096-
    character message limit.
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)

    from helpers.database import get_all_trades_for_user
    from helpers.formatters import format_tx_summary

    trades = get_all_trades_for_user(session.chat_id)

    if not trades:
        await update.message.reply_text(
            "📤 *Export*\n\nNo trades to export yet\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Build one string per trade and batch into ≤3800-char chunks.
    entries = [format_tx_summary(t) for t in trades]
    chunks: list[str] = []
    current = ""
    for entry in entries:
        block = entry + "\n\n"
        if len(current) + len(block) > 3800:
            chunks.append(current.rstrip())
            current = block
        else:
            current += block
    if current:
        chunks.append(current.rstrip())

    header = (
        f"📤 *Trade History Export*\n"
        f"{escape_md(str(len(trades)))} trades "
        f"\\({escape_md(str(len(chunks)))} message{'' if len(chunks) == 1 else 's'}\\)\n"
    )
    await update.message.reply_text(header, parse_mode=ParseMode.MARKDOWN_V2)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN_V2)


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------

async def settings_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /settings — view and change strategy, compounding, auto-execute, pause.
    """
    if not await _require_session(update):
        return

    from bot.keyboards import settings_menu_keyboard
    session = session_manager.get(update.effective_chat.id)

    await update.message.reply_text(
        "⚙️ *Settings*\n\n"
        f"Strategy: {escape_md(session.active_strategy.name)}\n"
        f"Compounding: {'On' if session.compound_enabled else 'Off'}\n"
        f"Execution: {'Auto' if session.auto_execute else 'Confirm'}\n"
        f"Max slippage: {escape_md(str(round(session.active_strategy.max_slippage * 100, 1)))}%\n"
        f"Paused: {'Yes' if session.paused else 'No'}\n\n"
        "_Tap a button to change a setting\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=settings_menu_keyboard(
            compound_enabled=session.compound_enabled,
            auto_execute=session.auto_execute,
        ),
    )


# ---------------------------------------------------------------------------
# /reset
# ---------------------------------------------------------------------------

async def reset_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /reset — clear the session, stop the scheduler, and wipe all user data.

    Shows a confirmation keyboard before proceeding (handled in callbacks.py).
    """
    if not await _require_session(update):
        return

    from bot.keyboards import reset_confirm_keyboard
    await update.message.reply_text(
        "⚠️ *Reset*\n\n"
        "This will:\n"
        "• Stop the bot scheduler\n"
        "• Clear your wallet from memory\n"
        "• Wipe your watchlist and alerts\n\n"
        "_Your transaction history in the database will be kept\\._\n\n"
        "Are you sure?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=reset_confirm_keyboard(),
    )


# ---------------------------------------------------------------------------
# /help — fully implemented in Sprint 3, unchanged
# ---------------------------------------------------------------------------

async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /help — comprehensive command guide and DeFi concept explainers.

    Optionally accepts a command name argument for focused help:
    /help dashboard — detailed help for /dashboard.
    """
    args = context.args
    if args:
        await _help_specific(update, args[0].lstrip("/").lower())
        return

    help_text = (
        "📖 *TGLP Bot — Help Guide*\n\n"
        "I automate liquidity pool management on PancakeSwap V3 \\(BSC Testnet\\)\\. "
        "Here is everything I can do:\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "*🚀 Getting Started*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/start — Set up your wallet and choose a strategy\\. "
        "You'll need a BSC Testnet private key and some testnet BNB\\.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "*📊 Portfolio*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/dashboard — Rich overview of your position: token amounts, unrealised "
        "P&L, total fees earned, gas spent, and system health status\\.\n\n"
        "/history — Paginated list of every transaction the bot has made for you, "
        "with BSCScan links and gas costs\\.\n\n"
        "/export — Download your full trade history as a formatted text summary\\.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "*⚡ Trading*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/allocate — Manually trigger an analysis and allocation cycle right now\\. "
        "Useful for testing or when you want immediate action\\.\n\n"
        "/explore — Browse the top pools that match your strategy\\. "
        "See APR, TVL, 24h volume, and fee tier for each pool\\.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "*🔔 Alerts & Watchlist*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/watch — Add a pool or token to your watchlist with a custom alert "
        "threshold \\(e\\.g\\., alert me if BNB/USDT APR drops below 5%\\)\\.\n\n"
        "/alerts — View and remove your active watchlist alerts\\.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "*⚙️ Configuration*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/settings — Change your strategy, toggle compounding, switch execution "
        "mode \\(auto vs\\. confirm\\), or adjust slippage tolerance\\.\n\n"
        "/reset — Clear your session and stop the bot\\. "
        "Your trade history is kept in the database\\.\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "*💡 DeFi Concepts*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Use /help followed by a concept name for a brief explanation:\n"
        "`/help lp` — Liquidity Pools\n"
        "`/help apr` — APR vs\\. APY\n"
        "`/help tvl` — Total Value Locked\n"
        "`/help il` — Impermanent Loss\n"
        "`/help v3` — PancakeSwap V3 concentrated liquidity\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "_TGLP Bot is an OCR A Level CS NEA project\\. "
        "All activity is on BSC Testnet — no real funds are involved\\._"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)


async def _help_specific(update: Update, topic: str) -> None:
    """Send focused help text for a specific command or DeFi concept."""
    explanations = {
        "dashboard": (
            "📊 */dashboard*\n\n"
            "Shows a snapshot of your current position and performance:\n"
            "• Token amounts in your LP position\n"
            "• Unrealised P&L \\(current value vs\\. entry value\\)\n"
            "• Total fees collected since you started\n"
            "• Total gas spent \\(BNB\\)\n"
            "• Net profit/loss \\(fees minus gas and slippage costs\\)\n"
            "• Number of rebalances performed\n"
            "• Your current strategy and settings\n"
            "• System health \\(RPC latency, last cycle time\\)"
        ),
        "allocate": (
            "⚡ */allocate*\n\n"
            "Triggers an immediate analysis cycle:\n"
            "1\\. Fetches fresh pool data from DeFiLlama\n"
            "2\\. Scores pools against your strategy\n"
            "3\\. Decides whether to allocate, rebalance, or compound\n"
            "4\\. Executes \\(or proposes, if auto\\-execute is off\\)\n\n"
            "The bot also runs this cycle automatically every 15 seconds\\."
        ),
        "explore": (
            "🔍 */explore*\n\n"
            "Browse pools without committing funds:\n"
            "• Shows the top pools matching your strategy\n"
            "• APR, TVL, 24h volume, fee tier, pair type\n"
            "• Tap any pool to see full details\n"
            "• 'Watch' button adds the pool to your watchlist"
        ),
        "watch": (
            "👁 */watch*\n\n"
            "Monitor a pool or token without putting money in it\\.\n\n"
            "Usage: `/watch` — starts a guided setup\\.\n\n"
            "You'll be asked to set a threshold type:\n"
            "• APR below X% — alert when yield drops too low\n"
            "• APR above X% — alert when a new opportunity appears\n"
            "• TVL below $X — alert when pool liquidity falls"
        ),
        "settings": (
            "⚙️ */settings*\n\n"
            "Adjust your bot configuration:\n"
            "• *Change Strategy* — switch between Conservative, Balanced, "
            "Aggressive, or Custom\n"
            "• *Compounding* — toggle auto\\-reinvestment of fees on/off\n"
            "• *Execution Mode* — auto \\(acts immediately\\) or confirm "
            "\\(asks before each trade\\)\n"
            "• *Slippage* — change your max acceptable slippage\n"
            "• *Pause/Resume* — pause the scheduler without losing your session"
        ),
        "reset": (
            "⚠️ */reset*\n\n"
            "Stops the bot and clears your session:\n"
            "• Stops the automated scheduler\n"
            "• Removes your wallet key from memory\n"
            "• Clears your watchlist from memory\n\n"
            "_Your transaction history in the database is kept\\._\n\n"
            "You must run /start to use the bot again after resetting\\."
        ),
        "lp": (
            "💧 *Liquidity Pools \\(LP\\)*\n\n"
            "A liquidity pool is a smart contract that holds pairs of tokens "
            "\\(e\\.g\\., BNB/USDT\\)\\. Traders swap between tokens using the pool, "
            "and pay a small fee \\(0\\.01% – 1%\\) on each swap\\.\n\n"
            "As a liquidity provider, you deposit both tokens into the pool\\. "
            "In return, you earn a share of all fees generated by trades in that pool\\.\n\n"
            "The more trading volume a pool has, the more fees you earn\\."
        ),
        "apr": (
            "📈 *APR vs\\. APY*\n\n"
            "*APR* \\(Annual Percentage Rate\\) is the yearly return without "
            "compounding\\. If a pool has 20% APR, you earn 20% of your "
            "deposit over a year if you never reinvest\\.\n\n"
            "*APY* \\(Annual Percentage Yield\\) includes the effect of "
            "compounding — reinvesting your earnings so they also earn fees\\. "
            "APY is always higher than APR for the same pool\\.\n\n"
            "TGLP Bot displays APR from DeFiLlama and calculates your "
            "effective APY based on your compounding frequency\\."
        ),
        "tvl": (
            "🏦 *Total Value Locked \\(TVL\\)*\n\n"
            "TVL is the total USD value of all tokens deposited in a pool\\. "
            "A pool with $10M TVL has $10M worth of tokens in it\\.\n\n"
            "*Why it matters:*\n"
            "• Higher TVL generally means more trading volume → more fees\n"
            "• Higher TVL means more liquidity to trade against → less slippage "
            "when you enter or exit\n"
            "• Very low TVL pools carry exit liquidity risk — you may not be "
            "able to withdraw your full position without heavy slippage\\."
        ),
        "il": (
            "⚠️ *Impermanent Loss \\(IL\\)*\n\n"
            "When you provide liquidity, the ratio of your two tokens changes "
            "as traders swap\\. If the price of one token moves significantly, "
            "you end up holding more of the falling token and less of the "
            "rising one — compared to just holding both tokens\\.\n\n"
            "This difference is called impermanent loss\\. It is 'impermanent' "
            "because if the price returns to the original ratio, the loss "
            "disappears\\.\n\n"
            "*How to reduce IL risk:*\n"
            "• Use stablecoin\\-stablecoin pools \\(Conservative strategy\\) — "
            "both tokens hold $1, so price ratio barely changes\n"
            "• Set tight tick ranges in V3 \\(more fees, but higher IL risk\\)"
        ),
        "v3": (
            "🥞 *PancakeSwap V3 — Concentrated Liquidity*\n\n"
            "V3 allows you to concentrate your liquidity within a specific "
            "price range \\(called ticks\\)\\. If trades happen within your range, "
            "you earn fees at a much higher rate than V2\\.\n\n"
            "If the price moves outside your range, your position stops "
            "earning fees until you rebalance it into the new price range\\.\n\n"
            "TGLP Bot monitors your position's tick range and rebalances "
            "automatically when it detects the price has moved enough to "
            "justify the gas cost of rebalancing\\."
        ),
    }

    text = explanations.get(
        topic,
        f"❓ No help entry found for `{escape_md(topic)}`\\.\n\n"
        "Try `/help` for the full command list, or one of:\n"
        "`/help lp` `/help apr` `/help tvl` `/help il` `/help v3`",
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
