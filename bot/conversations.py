"""
bot/conversations.py
====================
Multi-step conversation handlers beyond the onboarding flow.

1. **/watch setup** (fully implemented, Sprint 11)
   Three-state guided flow to add a pool or token to the watchlist:
     State 0 — WATCH_AWAITING_IDENTIFIER:
       User sends a pool address or token symbol.
     State 1 — WATCH_AWAITING_THRESHOLD_TYPE:
       User picks a threshold type from an inline keyboard
       (apr_above / apr_below / tvl_below).
     State 2 — WATCH_AWAITING_THRESHOLD_VALUE:
       User types the numeric threshold value.
       core.watchlist.add_watch_item() is called and the item is saved.

2. **Custom strategy editor** — placeholder, not yet implemented.
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


# ---------------------------------------------------------------------------
# /watch conversation — state constants
# ---------------------------------------------------------------------------

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
    """
    keyboard = [
        [InlineKeyboardButton("📈 APR above X%",   callback_data="wt_apr_above")],
        [InlineKeyboardButton("📉 APR below X%",   callback_data="wt_apr_below")],
        [InlineKeyboardButton("🏦 TVL below $X",   callback_data="wt_tvl_below")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# State 0 — entry: ask for identifier
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
        "\\(e\\.g\\. `WBNB`\\) you want to monitor\\.\n\n"
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
    type_label = "pool address" if item_type == "pool" else "token"
    await update.message.reply_text(
        f"Got it: *{escape_md(item_type)}* `{escape_md(identifier)}`\n\n"
        f"Now choose the *threshold type* — what condition should trigger the alert?",
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
    data = query.data  # "wt_apr_above", "wt_apr_below", or "wt_tvl_below"

    threshold_map = {
        "wt_apr_above": "apr_above",
        "wt_apr_below": "apr_below",
        "wt_tvl_below": "tvl_below",
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
    if threshold_type in ("apr_above", "apr_below"):
        prompt = (
            f"Enter the *APR threshold* as a percentage "
            f"\\(e\\.g\\. `5` for 5%\\):\n\n"
            f"Type /cancel to exit\\."
        )
    else:  # tvl_below
        prompt = (
            f"Enter the *TVL threshold* in USD "
            f"\\(e\\.g\\. `50000` for \\$50,000\\):\n\n"
            f"Type /cancel to exit\\."
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
        "apr_above": f"APR above {threshold_value:.2f}%",
        "apr_below": f"APR below {threshold_value:.2f}%",
        "tvl_below": f"TVL below ${threshold_value:,.0f}",
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


# ---------------------------------------------------------------------------
# Custom strategy editor — placeholder
# ---------------------------------------------------------------------------
# Triggered by "Change Strategy → Custom" in /settings.
# Not yet implemented; bot/app.py does not register this handler.

custom_strategy_handler = None
