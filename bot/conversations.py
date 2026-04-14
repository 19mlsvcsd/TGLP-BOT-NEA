"""
bot/conversations.py
====================
Multi-step conversation handlers beyond the onboarding flow.

This module will house ConversationHandlers for:

1. **/watch setup** — guided flow to add a pool or token to the watchlist
   with a custom alert threshold type and value.

2. **Custom strategy editor** — allows an already-onboarded user to rebuild
   their strategy parameters from /settings without needing to reset and
   re-run /start.

Both handlers are stubs in Sprint 3 and will be fully implemented in Sprint 11
when core/watchlist.py and the full settings system are available.

The ConversationHandler objects defined here are imported and registered in
bot/app.py alongside the onboarding handler.
"""

import logging

from telegram import Update
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


# ---------------------------------------------------------------------------
# /watch conversation — stub handlers
# ---------------------------------------------------------------------------

async def watch_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Entry point for the /watch conversation.

    Checks for a session, then asks the user what they want to monitor.
    Full implementation in Sprint 11.
    """
    from core.strategy_manager import session_manager
    if not session_manager.exists(update.effective_chat.id):
        await update.message.reply_text(
            "⚠️ Run /start first to set up your wallet\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👁 *Add to Watchlist*\n\n"
        "Send the pool address or token symbol you want to monitor\\.\n\n"
        "_Full watchlist setup will be available in Sprint 11\\._\n\n"
        "Type /cancel to exit\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WATCH_AWAITING_IDENTIFIER


async def watch_receive_identifier(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive pool/token identifier. Stub — full logic in Sprint 11."""
    identifier = update.message.text.strip() if update.message.text else ""
    context.user_data["watch_identifier"] = identifier

    await update.message.reply_text(
        f"Got it: `{identifier}`\n\n"
        "_Threshold selection coming in Sprint 11\\._\n\n"
        "Type /cancel to exit\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    # End the conversation here until Sprint 11 completes this flow.
    return ConversationHandler.END


async def watch_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Cancel the watch setup conversation."""
    if "watch_identifier" in context.user_data:
        del context.user_data["watch_identifier"]
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
            MessageHandler(filters.TEXT & ~filters.COMMAND, watch_receive_identifier)
        ],
    },
    fallbacks=[CommandHandler("cancel", watch_cancel)],
    allow_reentry=True,
    per_user=True,
    per_chat=False,
    per_message=False,
)


# ---------------------------------------------------------------------------
# Custom strategy editor — stub
# ---------------------------------------------------------------------------
# This will be wired up as a ConversationHandler in Sprint 11 triggered by
# the "Change Strategy → Custom" button in /settings.
# For now it is defined as a placeholder so bot/app.py can import it.

custom_strategy_handler = None  # Replaced in Sprint 11.
