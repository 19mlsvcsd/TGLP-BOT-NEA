"""
bot/app.py
==========
Telegram bot application setup and entry point.

Builds the Application object, registers all command and callback handlers,
and starts the polling loop. This module is the bridge between main.py and
the rest of the bot/ package.

Sprint 3 will implement this fully. This stub allows main.py to import and
call start_bot() without errors during Sprint 1 testing.
"""

import logging

logger = logging.getLogger(__name__)


def start_bot() -> None:
    """
    Placeholder for the Telegram polling loop.

    Replace this implementation in Sprint 3 with the full Application setup.
    """
    logger.info(
        "Bot stub: bot/app.py is not yet implemented. "
        "This placeholder will be replaced in Sprint 3."
    )
    print(
        "\n[TGLP Bot] Sprint 1 complete — skeleton verified.\n"
        "The Telegram bot will be wired up in Sprint 3.\n"
        "Press Ctrl+C to exit."
    )
    # Block so the operator can read the message.
    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutdown requested by operator.")
