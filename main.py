"""
main.py
=======
Entry point for TGLP Bot.

Responsibilities:
1. Load environment variables from .env
2. Configure logging
3. Initialise the SQLite database
4. Print a startup banner so the operator knows the bot has started
5. Hand off to bot/app.py to start the Telegram polling loop

This file is intentionally thin; all real logic lives in the modules it calls.
"""

import logging
import os
import sys

from dotenv import load_dotenv

# Load .env before importing anything that reads os.environ.
# This must happen at the very top of main.py.
load_dotenv()

from config.settings import LOG_FORMAT, LOG_DATE_FORMAT, LOG_LEVEL
from helpers.database import initialise_database


def configure_logging() -> None:
    """
    Set up the root logger to write to stdout with timestamps and severity.

    The log level is read from config/settings.py (default: INFO).
    Individual modules obtain child loggers via logging.getLogger(__name__).
    """
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=sys.stdout,
    )


def print_banner() -> None:
    """
    Print a startup banner to the console.

    Includes the bot name, version, and the network it is connecting to.
    This gives the operator an immediate visual confirmation that the process
    has started and which environment it is using.
    """
    banner = """
╔══════════════════════════════════════════════════════╗
║         TGLP Bot: Telegram LP Manager                ║
║         BSC Testnet / PancakeSwap V3                 ║
║   OCR A Level Computer Science NEA Project           ║
╚══════════════════════════════════════════════════════╝
"""
    print(banner)


def check_env() -> None:
    """
    Verify that required environment variables are set before starting.

    If TELEGRAM_BOT_TOKEN is missing, the bot cannot start at all, so we
    exit immediately with a clear error rather than letting it crash later
    with a confusing traceback.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print(
            "[ERROR] TELEGRAM_BOT_TOKEN is not set.\n"
            "Copy .env.example to .env and fill in your bot token from @BotFather."
        )
        sys.exit(1)

    rpc = os.getenv("BSC_TESTNET_RPC_URL")
    if not rpc:
        # Not fatal, settings.py has a default RPC URL.
        print(
            "[WARNING] BSC_TESTNET_RPC_URL is not set in .env. "
            "Using the default public endpoint from config/settings.py."
        )


def run_bot() -> None:
    """
    Start the Telegram bot polling loop.

    Imported here (not at module level) so that the import only happens after
    logging and database initialisation are complete.
    """
    from bot.app import start_bot
    start_bot()


def main() -> None:
    """
    Application entry point, called when main.py is run directly.

    Execution order:
    1. configure_logging(): set up log output
    2. print_banner(): show startup banner
    3. check_env(): fail fast if config is missing
    4. initialise_database(): create DB tables if not present
    5. run_bot(): start Telegram polling (blocks until Ctrl+C)
    """
    configure_logging()
    print_banner()
    check_env()

    logger = logging.getLogger(__name__)
    logger.info("Starting TGLP Bot...")

    try:
        initialise_database()
        logger.info("Database ready.")
    except Exception as e:
        logger.critical("Database initialisation failed: %s", e)
        sys.exit(1)

    logger.info("Launching Telegram bot...")
    run_bot()


if __name__ == "__main__":
    main()
