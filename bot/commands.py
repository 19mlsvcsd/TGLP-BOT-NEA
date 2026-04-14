"""
bot/commands.py
===============
Command handlers for TGLP Bot.

Each Telegram slash command has its handler function here. The /help command
is fully implemented in this sprint. All other commands return a descriptive
"coming in a future sprint" message for now and will be replaced with full
implementations in Sprints 9–11.

Handler registration happens in bot/app.py. Command functions here are kept
thin: they validate the user has a session, then delegate to the relevant
core module (portfolio, market_data, etc.) as those modules are built.
"""

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.strategy_manager import session_manager
from helpers.formatters import escape_md

logger = logging.getLogger(__name__)


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
    /dashboard — show portfolio overview, position details, P&L, and system health.

    Full implementation: Sprint 11 (requires core/portfolio.py from Sprint 9).
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)
    await update.message.reply_text(
        "📊 *Dashboard*\n\n"
        f"Wallet: `{escape_md(session.wallet_address)}`\n"
        f"Strategy: {escape_md(session.active_strategy.name)}\n"
        f"Position: {'Open' if session.has_position() else 'None'}\n"
        f"Status: {'⏸ Paused' if session.paused else ('🔒 Safety locked' if session.safety_locked else '✅ Active')}\n\n"
        "_Full dashboard with P&L and metrics coming in Sprint 11\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /allocate
# ---------------------------------------------------------------------------

async def allocate_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /allocate — manually trigger an analysis and allocation cycle.

    Full implementation: Sprint 11 (requires core/dispatcher.py from Sprint 10).
    """
    if not await _require_session(update):
        return

    await update.message.reply_text(
        "⚡ *Manual Allocation*\n\n"
        "This will trigger an immediate analysis cycle\\.\n\n"
        "_Manual allocation will be live in Sprint 11\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /explore
# ---------------------------------------------------------------------------

async def explore_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /explore — browse top pools filtered by the user's strategy.

    Full implementation: Sprint 11 (requires core/market_data.py from Sprint 4).
    """
    if not await _require_session(update):
        return

    session = session_manager.get(update.effective_chat.id)
    await update.message.reply_text(
        "🔍 *Pool Explorer*\n\n"
        f"Filtering for strategy: *{escape_md(session.active_strategy.name)}*\n\n"
        "_Live pool data and filtering will be available in Sprint 11\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /watch
# ---------------------------------------------------------------------------

async def watch_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /watch — add a pool or token to the watchlist with custom alert thresholds.

    Full implementation: Sprint 11 (requires core/watchlist.py from Sprint 9
    and the /watch ConversationHandler from bot/conversations.py).
    """
    if not await _require_session(update):
        return

    await update.message.reply_text(
        "👁 *Watchlist*\n\n"
        "Use: `/watch <pool_address_or_token>` to start monitoring\\.\n\n"
        "_Watchlist setup conversation will be available in Sprint 11\\._",
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

    Full implementation: Sprint 11 (requires core/alerts.py from Sprint 9).
    """
    if not await _require_session(update):
        return

    await update.message.reply_text(
        "🔔 *Active Alerts*\n\n"
        "_Alert management will be available in Sprint 11\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def history_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /history — paginated transaction history with filters.

    Full implementation: Sprint 11 (requires core/portfolio.py from Sprint 9).
    """
    if not await _require_session(update):
        return

    await update.message.reply_text(
        "📜 *Transaction History*\n\n"
        "_Your trade history will appear here in Sprint 11\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------

async def export_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /export — export full trade history as a formatted text/CSV output.

    Full implementation: Sprint 11 (requires core/portfolio.py from Sprint 9).
    """
    if not await _require_session(update):
        return

    await update.message.reply_text(
        "📤 *Export*\n\n"
        "_Trade history export will be available in Sprint 11\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------

async def settings_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /settings — view and change strategy, compounding, auto-execute, slippage.

    Full implementation: Sprint 11.
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
# /help — fully implemented
# ---------------------------------------------------------------------------

async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /help — comprehensive command guide and DeFi concept explainers.

    Optionally accepts a command name argument for focused help:
    /help dashboard — detailed help for /dashboard.
    """
    # Check if the user asked for help on a specific command.
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
    """
    Send focused help text for a specific command or DeFi concept.

    Args:
        update: The incoming Update.
        topic:  Lowercase command name or concept keyword.
    """
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
            "Usage: `/watch <pool_address>` or `/watch <TOKEN>`\n\n"
            "You'll be asked to set a threshold type:\n"
            "• APR below X% — alert when yield drops too low\n"
            "• APR above X% — alert when a new opportunity appears\n"
            "• Price moves more than X% in one cycle"
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
        "`/help lp` `//help apr` `/help tvl` `/help il` `/help v3`",
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
