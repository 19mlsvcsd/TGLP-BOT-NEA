"""
core/scheduler.py
=================
APScheduler wrapper for per-user cycle scheduling.

Each onboarded user gets one recurring job that fires every
CYCLE_INTERVAL_SECONDS seconds. The job calls the dispatcher's run_cycle()
function, which runs the full analysis-decision-execution pipeline for that
user.

Jobs are identified by the string f"user_{chat_id}" so that the scheduler
can locate, pause, resume, or remove them by chat ID without maintaining a
separate lookup table.

Design: BotScheduler wraps a BackgroundScheduler instance. The scheduler
runs in a daemon thread separate from the python-telegram-bot asyncio event
loop. Communication back to Telegram is handled through a notify_func callback
that the dispatcher calls; this decouples the cycle logic from async I/O.
"""

import logging
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import CYCLE_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


class BotScheduler:
    """
    Process-level scheduler for per-user cycle jobs.

    Wraps APScheduler's BackgroundScheduler. One instance is shared across
    the entire application via the `bot_scheduler` module-level singleton.

    Thread safety: APScheduler's BackgroundScheduler is thread-safe for add/
    remove/pause/resume operations. The cycle callbacks run in APScheduler's
    own thread pool (default: 1 thread) to avoid parallel cycles for the
    same user.
    """

    def __init__(self) -> None:
        # misfire_grace_time: if a job is late by up to CYCLE_INTERVAL_SECONDS,
        # still run it once. After that window, skip it and wait for the next
        # scheduled slot.
        self._scheduler = BackgroundScheduler(
            job_defaults={"misfire_grace_time": CYCLE_INTERVAL_SECONDS}
        )
        self._started: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start the background scheduler.

        Safe to call multiple times; no-op if already running.
        Called once during bot startup in bot/app.py.
        """
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("BotScheduler started.")

    def shutdown(self) -> None:
        """
        Stop the scheduler and cancel all pending jobs.

        Uses wait=False so shutdown does not block the calling thread.
        Called during bot teardown in bot/app.py.
        """
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("BotScheduler stopped.")

    @property
    def is_running(self) -> bool:
        """True if the scheduler has been started and not yet shut down."""
        return self._started

    # ------------------------------------------------------------------
    # Per-user job management
    # ------------------------------------------------------------------

    def add_user_job(self, chat_id: int, callback: Callable) -> None:
        """
        Register a recurring cycle job for a user.

        If a job already exists for this chat_id it is replaced so that
        calling this function after a /reset + /start sequence produces
        exactly one active job per user.

        The callback must be a zero-argument callable. Use functools.partial
        or a closure to bind session/w3/notify_func before passing it here.
        See dispatcher.build_cycle_callback() for a ready-made helper.

        Args:
            chat_id:  Telegram chat ID, used as the job identifier.
            callback: Zero-argument callable that runs one cycle.
        """
        job_id = f"user_{chat_id}"
        self._scheduler.add_job(
            callback,
            trigger=IntervalTrigger(seconds=CYCLE_INTERVAL_SECONDS),
            id=job_id,
            name=f"Cycle for user {chat_id}",
            replace_existing=True,
        )
        logger.info(
            "Cycle job registered for chat_id %d (every %ds).",
            chat_id, CYCLE_INTERVAL_SECONDS,
        )

    def remove_user_job(self, chat_id: int) -> bool:
        """
        Remove the cycle job for a user.

        Called when the user runs /reset. The session is also deleted, but
        removing the job prevents cycles from firing for a non-existent
        session.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if a job existed and was removed, False if no job was found.
        """
        job_id = f"user_{chat_id}"
        job = self._scheduler.get_job(job_id)
        if job is not None:
            self._scheduler.remove_job(job_id)
            logger.info("Cycle job removed for chat_id %d.", chat_id)
            return True
        logger.debug("remove_user_job: no job found for chat_id %d.", chat_id)
        return False

    def pause_user_job(self, chat_id: int) -> bool:
        """
        Pause (but do not remove) the cycle job for a user.

        Called by /settings when the user toggles pause ON. The job remains
        registered so it can be resumed later without re-creating it.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the job was found and paused, False if not found.
        """
        job = self._scheduler.get_job(f"user_{chat_id}")
        if job is not None:
            job.pause()
            logger.info("Cycle job paused for chat_id %d.", chat_id)
            return True
        return False

    def resume_user_job(self, chat_id: int) -> bool:
        """
        Resume a previously paused cycle job.

        Called by /settings when the user toggles pause OFF.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if the job was found and resumed, False if not found.
        """
        job = self._scheduler.get_job(f"user_{chat_id}")
        if job is not None:
            job.resume()
            logger.info("Cycle job resumed for chat_id %d.", chat_id)
            return True
        return False

    def has_job(self, chat_id: int) -> bool:
        """
        Return True if an active (or paused) job exists for this chat_id.

        Args:
            chat_id: Telegram chat ID.
        """
        return self._scheduler.get_job(f"user_{chat_id}") is not None

    def active_job_count(self) -> int:
        """Return the total number of registered jobs (running + paused)."""
        return len(self._scheduler.get_jobs())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

# Import with:  from core.scheduler import bot_scheduler
bot_scheduler = BotScheduler()
