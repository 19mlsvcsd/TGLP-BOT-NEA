"""
bot/app.py
==========
Telegram bot application setup and polling loop.

This module is the integration point for the entire bot/ package. It:
1. Builds the python-telegram-bot Application object with the bot token.
2. Registers all handlers in the correct priority order.
3. Wires up the Web3 connection, notify_func, and BotScheduler via post_init.
4. Starts the polling loop (blocking call — runs until Ctrl+C).

Handler registration order matters in python-telegram-bot:
- ConversationHandlers must be registered BEFORE generic CommandHandlers,
  because both will match /start — the ConversationHandler must win.
- The generic CallbackQueryHandler (callbacks.py) must be registered LAST
  as a catch-all for any callback not handled by a ConversationHandler.

Role in the system: main.py calls start_bot() here. Nothing else calls
this module. All other modules are decoupled from the Application object.
"""

import asyncio
import logging
import os

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from bot.callbacks import handle_callback
from bot.commands import (
    alerts_command,
    allocate_command,
    dashboard_command,
    export_command,
    explore_command,
    help_command,
    history_command,
    reset_command,
    settings_command,
    watch_command,
)
from bot.conversations import watch_conversation_handler
from bot.onboarding import onboarding_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

async def _post_init(application: Application) -> None:
    """
    Called by python-telegram-bot after the Application is fully initialised
    but before polling starts.

    Responsibilities:
      1. Connect to BSC Testnet via Web3 and store the instance in bot_data
         so every command handler can access it without creating its own
         connection.
      2. Build a notify_func closure that bridges the APScheduler background
         thread to the asyncio event loop via run_coroutine_threadsafe.
      3. Start the BotScheduler background thread.

    All data is stored in application.bot_data so handlers can retrieve it
    with:  context.application.bot_data["w3"]
           context.application.bot_data["notify_func"]
    """
    from helpers.blockchain import get_web3
    from core.scheduler import bot_scheduler

    # Web3 connection — one instance shared across all cycles and commands.
    w3 = get_web3()
    application.bot_data["w3"] = w3

    # Capture the running event loop and the bot reference so the closure
    # can schedule Telegram sends from the APScheduler background thread.
    loop = asyncio.get_event_loop()
    bot_obj = application.bot

    def notify_func(chat_id: int, text: str) -> None:
        """
        Send a Telegram message from any thread.

        The dispatcher calls this from APScheduler's background thread.
        run_coroutine_threadsafe queues the coroutine on the asyncio loop
        that owns the Telegram bot connection.
        """
        asyncio.run_coroutine_threadsafe(
            bot_obj.send_message(chat_id=chat_id, text=text),
            loop,
        )

    application.bot_data["notify_func"] = notify_func

    # Start the background scheduler — safe to call even if already started.
    bot_scheduler.start()
    logger.info("post_init complete: Web3 connected, notify_func ready, scheduler started.")


async def _post_shutdown(application: Application) -> None:
    """
    Called by python-telegram-bot during graceful shutdown.

    Stops the BotScheduler so all pending cycle jobs are cancelled and the
    background thread exits cleanly.
    """
    from core.scheduler import bot_scheduler
    bot_scheduler.shutdown()
    logger.info("post_shutdown: scheduler stopped.")


def _build_application() -> Application:
    """
    Construct and configure the Application instance.

    Reads the bot token from the TELEGRAM_BOT_TOKEN environment variable
    (guaranteed to be set by main.py's check_env() before this is called).

    Returns:
        A fully configured Application ready to start polling.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "This should have been caught by main.py — check check_env()."
        )

    app = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # ── Register handlers ─────────────────────────────────────────────────
    # Order is significant. Handlers are checked in registration order.

    # 1. Onboarding ConversationHandler — must be first so /start enters the
    #    conversation rather than hitting the fallback CommandHandler.
    app.add_handler(onboarding_handler)

    # 2. /watch ConversationHandler — before the generic /watch CommandHandler
    #    stub so that the conversation takes over when the user is mid-flow.
    app.add_handler(watch_conversation_handler)

    # 3. Standalone command handlers.
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("allocate",  allocate_command))
    app.add_handler(CommandHandler("explore",   explore_command))
    app.add_handler(CommandHandler("alerts",    alerts_command))
    app.add_handler(CommandHandler("history",   history_command))
    app.add_handler(CommandHandler("export",    export_command))
    app.add_handler(CommandHandler("settings",  settings_command))
    app.add_handler(CommandHandler("reset",     reset_command))
    app.add_handler(CommandHandler("help",      help_command))

    # 4. Catch-all CallbackQueryHandler — must be LAST. Handles all inline
    #    button presses that are not owned by a ConversationHandler.
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("All handlers registered.")
    return app


def start_bot() -> None:
    """
    Build the Application and start the polling loop.

    This function blocks until the process receives a shutdown signal
    (Ctrl+C / SIGTERM). python-telegram-bot handles the asyncio event loop
    internally via run_polling().

    Replaces the stub from Sprint 1.
    """
    logger.info("Building Telegram Application...")
    app = _build_application()

    logger.info("Starting polling loop. Press Ctrl+C to stop.")
    app.run_polling(
        # Drop any updates that arrived while the bot was offline.
        # This prevents a flood of stale messages from being processed
        # every time the bot restarts during development.
        drop_pending_updates=True,
    )
    logger.info("Bot stopped.")
