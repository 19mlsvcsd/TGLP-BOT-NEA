"""
bot/conversations.py
====================
Multi-step conversation handlers beyond the onboarding flow.

1. **/watch setup** (fully implemented, Sprint 11)
   Three-state guided flow to add a pool or token to the watchlist:
     State 0 (WATCH_AWAITING_IDENTIFIER):
       User sends a pool address or token symbol.
     State 1 (WATCH_AWAITING_THRESHOLD_TYPE):
       User picks a threshold type from an inline keyboard
       (apr_above / apr_below / tvl_below / price_change_pct).
     State 2 (WATCH_AWAITING_THRESHOLD_VALUE):
       User types the numeric threshold value.
       core.watchlist.add_watch_item() is called and the item is saved.

2. **Custom strategy editor** (Sprint 13)
   Seven-state guided flow triggered by /customstrategy or the
   "Custom Strategy" option in /settings → Change Strategy:
     State 0 (CUST_PAIRS):       Choose allowed pair types.
     State 1 (CUST_MIN_TVL):     Enter minimum TVL in USD.
     State 2 (CUST_SLIPPAGE):    Enter max slippage percentage.
     State 3 (CUST_REBAL):       Enter rebalance threshold (0.05-0.50).
     State 4 (CUST_COMPOUND):    Choose compound interval or disable.
     State 5 (CUST_AUTOEXEC):    Choose auto-execute or confirm mode.
     State 6 (CUST_CONFIRM):     Review summary and confirm or cancel.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# /watch conversation
# ===========================================================================

# State constants
(
    WATCH_AWAITING_IDENTIFIER,
    WATCH_AWAITING_THRESHOLD_TYPE,
    WATCH_AWAITING_THRESHOLD_VALUE,
) = range(3)

# user_data keys used internally by this conversation.
_UD_IDENTIFIER = "watch_identifier"
_UD_ITEM_TYPE  = "watch_item_type"   # "pool" or "token"
_UD_THRESHOLD  = "watch_threshold_type"


# ---------------------------------------------------------------------------
# Threshold type keyboard
# ---------------------------------------------------------------------------

def _threshold_type_keyboard() -> InlineKeyboardMarkup:
    """
    Inline keyboard for choosing which metric to watch.

    Callback data is the threshold_type string as used by core/alerts.py.
    'wt_price_change' maps to 'price_change_pct' for BNB price-change alerts.
    """
    keyboard = [
        [InlineKeyboardButton("📈 APR above X%",         callback_data="wt_apr_above")],
        [InlineKeyboardButton("📉 APR below X%",          callback_data="wt_apr_below")],
        [InlineKeyboardButton("🏦 TVL below $X",          callback_data="wt_tvl_below")],
        [InlineKeyboardButton("💰 BNB price change ≥X%",  callback_data="wt_price_change")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# State 0: ask for identifier
# ---------------------------------------------------------------------------

async def watch_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Entry point for the /watch conversation.

    Checks the user has an active session, then asks them to type the pool
    address or token symbol they want to monitor.
    """
    from core.strategy_manager import session_manager
    if not session_manager.exists(update.effective_chat.id):
        await update.message.reply_text(
            "⚠️ Run /start first to set up your wallet\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    # Clear any leftover data from a previous cancelled flow.
    for key in (_UD_IDENTIFIER, _UD_ITEM_TYPE, _UD_THRESHOLD):
        context.user_data.pop(key, None)

    await update.message.reply_text(
        "👁 *Add to Watchlist*\n\n"
        "Send the *pool address* \\(0x…\\) or *token symbol* "
        "\\(e\\.g\\. `WBNB` or `BNB`\\) you want to monitor\\.\n\n"
        "Type /cancel to exit\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WATCH_AWAITING_IDENTIFIER


# ---------------------------------------------------------------------------
# State 0 → 1: receive identifier, show threshold type keyboard
# ---------------------------------------------------------------------------

async def watch_receive_identifier(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive and store the pool address or token symbol.

    Determines item_type ('pool' if it looks like an address, 'token'
    otherwise) and advances to the threshold-type selection step.
    """
    identifier = (update.message.text or "").strip()
    if not identifier:
        await update.message.reply_text(
            "⚠️ Please send a pool address or token symbol\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return WATCH_AWAITING_IDENTIFIER

    # Classify as pool address (starts with 0x and ≥ 10 chars) or token symbol.
    if identifier.startswith("0x") and len(identifier) >= 10:
        item_type = "pool"
    else:
        item_type = "token"

    context.user_data[_UD_IDENTIFIER] = identifier
    context.user_data[_UD_ITEM_TYPE]  = item_type

    from helpers.formatters import escape_md
    await update.message.reply_text(
        f"Got it: *{escape_md(item_type)}* `{escape_md(identifier)}`\n\n"
        f"Now choose the *threshold type*. What condition should trigger the alert?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=_threshold_type_keyboard(),
    )
    return WATCH_AWAITING_THRESHOLD_TYPE


# ---------------------------------------------------------------------------
# State 1 → 2: receive threshold type, ask for the numeric value
# ---------------------------------------------------------------------------

async def watch_receive_threshold_type(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive the threshold type from the inline keyboard.

    Maps the callback_data suffix to the threshold_type string used by
    core/alerts.py and asks for the numeric threshold value.
    """
    query = update.callback_query
    await query.answer()
    data = query.data  # "wt_apr_above", "wt_apr_below", "wt_tvl_below", "wt_price_change"

    threshold_map = {
        "wt_apr_above":    "apr_above",
        "wt_apr_below":    "apr_below",
        "wt_tvl_below":    "tvl_below",
        "wt_price_change": "price_change_pct",
    }
    threshold_type = threshold_map.get(data)
    if threshold_type is None:
        await query.edit_message_text(
            "⚠️ Unknown threshold type\\. Type /cancel and try again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    context.user_data[_UD_THRESHOLD] = threshold_type

    # Prompt for the numeric value, giving the right unit hint.
    if threshold_type == "apr_above":
        prompt = (
            "Enter the *APR threshold* as a percentage "
            "\\(e\\.g\\. `20` for 20%\\), alert fires when APR rises *above* this:\n\n"
            "Type /cancel to exit\\."
        )
    elif threshold_type == "apr_below":
        prompt = (
            "Enter the *APR threshold* as a percentage "
            "\\(e\\.g\\. `5` for 5%\\), alert fires when APR drops *below* this:\n\n"
            "Type /cancel to exit\\."
        )
    elif threshold_type == "tvl_below":
        prompt = (
            "Enter the *TVL threshold* in USD "
            "\\(e\\.g\\. `50000` for \\$50,000\\), alert fires when TVL drops *below* this:\n\n"
            "Type /cancel to exit\\."
        )
    else:  # price_change_pct
        prompt = (
            "Enter the *price change threshold* as a percentage "
            "\\(e\\.g\\. `5` for 5%\\), alert fires when BNB price moves "
            "by *at least* this amount in one cycle:\n\n"
            "Type /cancel to exit\\."
        )

    await query.edit_message_text(prompt, parse_mode=ParseMode.MARKDOWN_V2)
    return WATCH_AWAITING_THRESHOLD_VALUE


# ---------------------------------------------------------------------------
# State 2 → END: receive numeric value, save the watch item
# ---------------------------------------------------------------------------

async def watch_receive_threshold_value(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive and validate the numeric threshold value.

    On success, calls core.watchlist.add_watch_item() to persist the alert
    and sends a confirmation message.
    """
    raw = (update.message.text or "").strip()
    try:
        threshold_value = float(raw)
        if threshold_value < 0:
            raise ValueError("negative threshold")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a positive number \\(e\\.g\\. `5` or `50000`\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return WATCH_AWAITING_THRESHOLD_VALUE

    identifier     = context.user_data.get(_UD_IDENTIFIER, "")
    item_type      = context.user_data.get(_UD_ITEM_TYPE, "pool")
    threshold_type = context.user_data.get(_UD_THRESHOLD, "apr_below")

    from core.strategy_manager import session_manager
    from core.watchlist import add_watch_item
    from helpers.formatters import escape_md

    session = session_manager.get(update.effective_chat.id)
    if session is None:
        await update.message.reply_text(
            "⚠️ Session expired\\. Run /start to set up\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    try:
        watch_id = add_watch_item(
            session,
            item_type=item_type,
            identifier=identifier,
            threshold_type=threshold_type,
            threshold_value=threshold_value,
        )
    except Exception as exc:
        logger.exception("watch_receive_threshold_value: add_watch_item failed: %s", exc)
        await update.message.reply_text(
            f"❌ Could not save the alert: `{escape_md(str(exc))}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    # Human-readable confirmation message.
    threshold_labels = {
        "apr_above":       f"APR above {threshold_value:.2f}%",
        "apr_below":       f"APR below {threshold_value:.2f}%",
        "tvl_below":       f"TVL below ${threshold_value:,.0f}",
        "price_change_pct": f"BNB price change ≥ {threshold_value:.2f}%",
    }
    condition_str = threshold_labels.get(threshold_type, threshold_type)

    await update.message.reply_text(
        f"✅ *Watchlist alert added* \\(ID: {watch_id}\\)\n\n"
        f"Monitoring *{escape_md(item_type)}*: `{escape_md(identifier)}`\n"
        f"Condition: {escape_md(condition_str)}\n\n"
        f"You'll be notified each cycle this threshold is crossed\\.\n"
        f"Use /alerts to view or remove your alerts\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    # Clean up conversation data.
    for key in (_UD_IDENTIFIER, _UD_ITEM_TYPE, _UD_THRESHOLD):
        context.user_data.pop(key, None)

    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel handler
# ---------------------------------------------------------------------------

async def watch_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel the /watch setup conversation at any state."""
    for key in (_UD_IDENTIFIER, _UD_ITEM_TYPE, _UD_THRESHOLD):
        context.user_data.pop(key, None)
    await update.message.reply_text(
        "❌ Watch setup cancelled\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /watch ConversationHandler
# ---------------------------------------------------------------------------

watch_conversation_handler = ConversationHandler(
    entry_points=[CommandHandler("watch", watch_start)],
    states={
        WATCH_AWAITING_IDENTIFIER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, watch_receive_identifier),
        ],
        WATCH_AWAITING_THRESHOLD_TYPE: [
            CallbackQueryHandler(watch_receive_threshold_type, pattern="^wt_"),
        ],
        WATCH_AWAITING_THRESHOLD_VALUE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, watch_receive_threshold_value),
        ],
    },
    fallbacks=[CommandHandler("cancel", watch_cancel)],
    allow_reentry=True,
    per_user=True,
    per_chat=False,
    per_message=False,
)


# ===========================================================================
# /customstrategy conversation (Sprint 13)
# ===========================================================================

# State constants (0-based, separate from /watch states)
(
    CUST_PAIRS,
    CUST_MIN_TVL,
    CUST_SLIPPAGE,
    CUST_REBAL,
    CUST_COMPOUND,
    CUST_AUTOEXEC,
    CUST_CONFIRM,
) = range(7)

# user_data keys for custom strategy intermediate values
_CS_PAIRS     = "cs_allowed_pairs"
_CS_MIN_TVL   = "cs_min_tvl"
_CS_SLIPPAGE  = "cs_max_slippage"
_CS_REBAL     = "cs_rebal_threshold"
_CS_COMPOUND  = "cs_compound_interval"  # int seconds or None
_CS_AUTOEXEC  = "cs_auto_execute"


def _cs_cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all intermediate custom-strategy keys from user_data."""
    for key in (_CS_PAIRS, _CS_MIN_TVL, _CS_SLIPPAGE, _CS_REBAL, _CS_COMPOUND, _CS_AUTOEXEC):
        context.user_data.pop(key, None)


# ---------------------------------------------------------------------------
# State 0: Pair type selection
# ---------------------------------------------------------------------------

async def custom_strat_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Entry point for the /customstrategy conversation.

    Can be triggered by the /customstrategy command or by the
    'cfg_strat_custom' callback from /settings → Change Strategy.
    """
    from core.strategy_manager import session_manager
    from bot.keyboards import custom_pairs_keyboard

    # Determine if this was triggered by a callback query or a command.
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id
        if not session_manager.exists(chat_id):
            await query.edit_message_text(
                "⚠️ Run /start first to set up your wallet\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return ConversationHandler.END
        _cs_cleanup(context)
        await query.edit_message_text(
            "🔧 *Custom Strategy Setup*\n\n"
            "*Step 1 of 6:* Choose which *pair types* to target:\n\n"
            "• Stable–Stable: USDT/USDC pools \\(lowest risk, lower APR\\)\n"
            "• Stable \\+ Large\\-Cap: includes BNB/USDT pools\n"
            "• Large\\-Cap only: BNB/ETH etc\\. \\(higher volatility\\)\n"
            "• All pairs: maximum pool selection",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=custom_pairs_keyboard(),
        )
    else:
        chat_id = update.effective_chat.id
        if not session_manager.exists(chat_id):
            await update.message.reply_text(
                "⚠️ Run /start first to set up your wallet\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return ConversationHandler.END
        _cs_cleanup(context)
        await update.message.reply_text(
            "🔧 *Custom Strategy Setup*\n\n"
            "*Step 1 of 6:* Choose which *pair types* to target:\n\n"
            "• Stable–Stable: USDT/USDC pools \\(lowest risk, lower APR\\)\n"
            "• Stable \\+ Large\\-Cap: includes BNB/USDT pools\n"
            "• Large\\-Cap only: BNB/ETH etc\\. \\(higher volatility\\)\n"
            "• All pairs: maximum pool selection\n\n"
            "Type /cancel to exit\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=custom_pairs_keyboard(),
        )
    return CUST_PAIRS


# ---------------------------------------------------------------------------
# State 0 → 1: receive pair types, ask for min TVL
# ---------------------------------------------------------------------------

_PAIRS_MAP = {
    "ob_pairs_stable":   ["stable-stable"],
    "ob_pairs_mixed":    ["stable-stable", "stable-largecap"],
    "ob_pairs_largecap": ["largecap-largecap"],
    "ob_pairs_all":      ["stable-stable", "stable-largecap", "largecap-largecap", "other"],
}

async def custom_strat_pairs(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive pair type selection from the keyboard."""
    query = update.callback_query
    await query.answer()

    pairs = _PAIRS_MAP.get(query.data)
    if pairs is None:
        await query.edit_message_text(
            "⚠️ Unknown selection\\. Type /cancel and start again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    context.user_data[_CS_PAIRS] = pairs

    labels = {
        "ob_pairs_stable":   "Stable–Stable only",
        "ob_pairs_mixed":    "Stable \\+ Large\\-Cap",
        "ob_pairs_largecap": "Large\\-Cap only",
        "ob_pairs_all":      "All pairs",
    }
    choice = labels.get(query.data, query.data)

    await query.edit_message_text(
        f"✅ Pair types: *{choice}*\n\n"
        f"*Step 2 of 6:* Enter the *minimum pool TVL* in USD\\.\n"
        f"Pools with TVL below this are excluded from scoring\\.\n\n"
        f"Typical values: `50000` \\(\\$50k\\), `100000` \\(\\$100k\\)\n\n"
        f"Type /cancel to exit\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return CUST_MIN_TVL


# ---------------------------------------------------------------------------
# State 1 → 2: receive min TVL, ask for max slippage
# ---------------------------------------------------------------------------

async def custom_strat_min_tvl(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive minimum TVL input."""
    raw = (update.message.text or "").strip().replace(",", "")
    try:
        min_tvl = float(raw)
        if min_tvl < 0:
            raise ValueError("negative")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a positive number \\(e\\.g\\. `50000`\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return CUST_MIN_TVL

    context.user_data[_CS_MIN_TVL] = min_tvl

    from helpers.formatters import escape_md
    await update.message.reply_text(
        f"✅ Min TVL: *\\${escape_md(f'{min_tvl:,.0f}')}*\n\n"
        f"*Step 3 of 6:* Enter the *maximum slippage* you accept \\(as a %\\)\\.\n"
        f"Swaps that would cause more slippage are rejected\\.\n\n"
        f"Typical values: `0.5` \\(0\\.5%\\), `1` \\(1%\\), `2` \\(2%\\)\n"
        f"Allowed range: 0\\.1% – 5%\n\n"
        f"Type /cancel to exit\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return CUST_SLIPPAGE


# ---------------------------------------------------------------------------
# State 2 → 3: receive slippage, ask for rebalance threshold
# ---------------------------------------------------------------------------

async def custom_strat_slippage(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive max slippage input."""
    raw = (update.message.text or "").strip()
    try:
        slippage_pct = float(raw)
        if not (0.1 <= slippage_pct <= 5.0):
            raise ValueError("out of range")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a slippage between `0.1` and `5` \\(percent\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return CUST_SLIPPAGE

    context.user_data[_CS_SLIPPAGE] = slippage_pct / 100.0  # store as fraction

    from helpers.formatters import escape_md
    await update.message.reply_text(
        f"✅ Max slippage: *{escape_md(f'{slippage_pct:.1f}')}%*\n\n"
        f"*Step 4 of 6:* Enter the *rebalance threshold*\\.\n"
        f"This is the minimum score gap between the current pool and the best "
        f"alternative before the bot triggers a rebalance\\.\n\n"
        f"Typical values: `0.10` \\(aggressive\\), `0.15` \\(balanced\\), `0.20` \\(conservative\\)\n"
        f"Allowed range: `0.05` – `0.50`\n\n"
        f"Type /cancel to exit\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return CUST_REBAL


# ---------------------------------------------------------------------------
# State 3 → 4: receive rebalance threshold, ask for compound interval
# ---------------------------------------------------------------------------

async def custom_strat_rebal(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive rebalance threshold input."""
    raw = (update.message.text or "").strip()
    try:
        threshold = float(raw)
        if not (0.05 <= threshold <= 0.50):
            raise ValueError("out of range")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please enter a value between `0.05` and `0.50`\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return CUST_REBAL

    context.user_data[_CS_REBAL] = threshold

    from bot.keyboards import custom_compound_interval_keyboard
    from helpers.formatters import escape_md
    await update.message.reply_text(
        f"✅ Rebalance threshold: *{escape_md(str(threshold))}*\n\n"
        f"*Step 5 of 6:* Choose your *compound interval*\\.\n"
        f"The bot will collect and reinvest fees at this frequency\\.\n"
        f"Select *Disabled* to compound manually\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=custom_compound_interval_keyboard(),
    )
    return CUST_COMPOUND


# ---------------------------------------------------------------------------
# State 4 → 5: receive compound interval, ask for auto-execute
# ---------------------------------------------------------------------------

async def custom_strat_compound(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive compound interval from the keyboard."""
    query = update.callback_query
    await query.answer()

    data = query.data  # "ob_compound_1800", "ob_compound_3600", etc., or "ob_compound_none"

    if data == "ob_compound_none":
        interval = None
        interval_label = "Disabled"
    else:
        try:
            interval = int(data.replace("ob_compound_", ""))
            minutes = interval // 60
            interval_label = f"Every {minutes} min"
        except (ValueError, AttributeError):
            await query.edit_message_text(
                "⚠️ Unknown selection\\. Type /cancel and start again\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return ConversationHandler.END

    context.user_data[_CS_COMPOUND] = interval

    from bot.keyboards import autoexecute_keyboard
    from helpers.formatters import escape_md
    await query.edit_message_text(
        f"✅ Compound interval: *{escape_md(interval_label)}*\n\n"
        f"*Step 6 of 6:* Choose your *execution mode*\\.\n\n"
        f"• *Auto\\-execute*: the bot acts immediately when a decision is made\\.\n"
        f"• *Confirm*: the bot sends a proposal and waits for your tap to proceed\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=autoexecute_keyboard(),
    )
    return CUST_AUTOEXEC


# ---------------------------------------------------------------------------
# State 5 → 6: receive auto-execute choice, show summary
# ---------------------------------------------------------------------------

async def custom_strat_autoexec(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive execution mode and show the full strategy summary."""
    query = update.callback_query
    await query.answer()

    auto_execute = query.data == "ob_exec_auto"
    context.user_data[_CS_AUTOEXEC] = auto_execute

    # Build summary for review.
    pairs = context.user_data.get(_CS_PAIRS, [])
    min_tvl = context.user_data.get(_CS_MIN_TVL, 0)
    slippage = context.user_data.get(_CS_SLIPPAGE, 0.01)
    rebal = context.user_data.get(_CS_REBAL, 0.15)
    compound_interval = context.user_data.get(_CS_COMPOUND)
    exec_mode = "Auto\\-execute" if auto_execute else "Confirm"
    compound_str = "Disabled" if compound_interval is None else f"Every {compound_interval // 60} min"

    from helpers.formatters import escape_md

    summary = (
        f"🔧 *Custom Strategy Summary*\n\n"
        f"Pair types: {escape_md(', '.join(pairs))}\n"
        f"Min TVL: \\${escape_md(f'{min_tvl:,.0f}')}\n"
        f"Max slippage: {escape_md(f'{slippage * 100:.1f}')}%\n"
        f"Rebalance threshold: {escape_md(str(rebal))}\n"
        f"Compound: {escape_md(compound_str)}\n"
        f"Execution: {exec_mode}\n\n"
        f"Tap *Confirm* to apply or *Cancel* to discard\\."
    )

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="cs_confirm"),
            InlineKeyboardButton("❌ Cancel",  callback_data="cs_cancel"),
        ]
    ])

    await query.edit_message_text(
        summary,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )
    return CUST_CONFIRM


# ---------------------------------------------------------------------------
# State 6 → END: confirm or cancel
# ---------------------------------------------------------------------------

async def custom_strat_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Confirm the custom strategy and apply it to the session.

    Builds a StrategyConfig from the collected values and assigns it to
    session.active_strategy.
    """
    query = update.callback_query
    await query.answer()

    if query.data == "cs_cancel":
        _cs_cleanup(context)
        await query.edit_message_text(
            "❌ Custom strategy setup cancelled\\. Your existing strategy is unchanged\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    # Build the StrategyConfig from collected values.
    from config.settings import StrategyConfig
    from core.strategy_manager import session_manager

    pairs    = context.user_data.get(_CS_PAIRS, ["stable-stable"])
    min_tvl  = context.user_data.get(_CS_MIN_TVL, 100_000.0)
    slippage = context.user_data.get(_CS_SLIPPAGE, 0.01)
    rebal    = context.user_data.get(_CS_REBAL, 0.15)
    compound = context.user_data.get(_CS_COMPOUND)  # int or None
    auto_ex  = context.user_data.get(_CS_AUTOEXEC, False)

    custom_config = StrategyConfig(
        name="Custom",
        description="User-defined custom strategy",
        allowed_pair_types=pairs,
        min_tvl_usd=min_tvl,
        max_slippage=slippage,
        rebalance_threshold=rebal,
        compound_interval=compound,
        auto_execute=auto_ex,
    )

    session = session_manager.get(update.effective_chat.id)
    if session is None:
        await query.edit_message_text(
            "⚠️ Session expired\\. Run /start to set up\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        _cs_cleanup(context)
        return ConversationHandler.END

    session.active_strategy = custom_config
    session.auto_execute    = auto_ex
    session.compound_enabled = compound is not None

    _cs_cleanup(context)

    await query.edit_message_text(
        "✅ *Custom strategy applied\\!*\n\n"
        "Your bot will now use the custom parameters for all future cycles\\.\n"
        "Use /dashboard to check the updated strategy or /settings to change it again\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel handler for /customstrategy
# ---------------------------------------------------------------------------

async def custom_strat_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel the custom strategy setup at any state."""
    _cs_cleanup(context)
    await update.message.reply_text(
        "❌ Custom strategy setup cancelled\\. Your existing strategy is unchanged\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /customstrategy ConversationHandler
# ---------------------------------------------------------------------------

custom_strategy_handler = ConversationHandler(
    entry_points=[
        CommandHandler("customstrategy", custom_strat_start),
        CallbackQueryHandler(custom_strat_start, pattern="^cfg_strat_custom$"),
    ],
    states={
        CUST_PAIRS: [
            CallbackQueryHandler(custom_strat_pairs, pattern="^ob_pairs_"),
        ],
        CUST_MIN_TVL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_strat_min_tvl),
        ],
        CUST_SLIPPAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_strat_slippage),
        ],
        CUST_REBAL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, custom_strat_rebal),
        ],
        CUST_COMPOUND: [
            CallbackQueryHandler(custom_strat_compound, pattern="^ob_compound_"),
        ],
        CUST_AUTOEXEC: [
            CallbackQueryHandler(custom_strat_autoexec, pattern="^ob_exec_"),
        ],
        CUST_CONFIRM: [
            CallbackQueryHandler(custom_strat_confirm, pattern="^cs_"),
        ],
    },
    fallbacks=[CommandHandler("cancel", custom_strat_cancel)],
    allow_reentry=True,
    per_user=True,
    per_chat=False,
    per_message=False,
)
