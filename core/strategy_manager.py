"""
core/strategy_manager.py
========================
Per-user session state and strategy profile management.

This module owns two key concepts:

1. **StrategyProfile** — a named bundle of trading parameters (pair types,
   TVL floor, slippage ceiling, rebalance threshold, compound interval).
   The four built-in profiles are defined in config/settings.py; this module
   re-exports the type so the rest of the codebase has a single import path.

2. **UserSession** — all runtime state for one Telegram user: their wallet
   address, private key (in-memory only), chosen strategy, position details,
   P&L accumulators, and scheduler handle.

3. **SessionManager** — a process-level dictionary of chat_id → UserSession.
   The module-level `session_manager` singleton is the single source of truth
   for who is currently onboarded. All modules that need session data import
   it from here.

Security note: private keys exist only in `UserSession.private_key` (RAM).
They are never written to disk, never logged, and never appear in any
exception message. The session is destroyed when the user runs /reset.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.settings import StrategyConfig

logger = logging.getLogger(__name__)

# Re-export StrategyConfig under the name used in the rest of the codebase.
# The underlying dataclass lives in config/settings.py so that all parameter
# values stay in the same place as other constants.
StrategyProfile = StrategyConfig


# ---------------------------------------------------------------------------
# UserSession
# ---------------------------------------------------------------------------

@dataclass
class UserSession:
    """
    All runtime state for a single onboarded Telegram user.

    Created during the /start onboarding flow and stored in SessionManager.
    Never persisted to disk — if the process restarts, users must re-run
    /start. (This is intentional: the private key must not be stored on disk.)

    Attributes:
        chat_id:               Telegram chat ID (integer). Used as the primary
                               key in SessionManager and in all DB records.
        wallet_address:        Public checksummed address derived from the key.
        private_key:           Raw 64-char hex key. RAM ONLY — never log this.
        active_strategy:       The StrategyConfig currently governing decisions.
        compound_enabled:      Whether auto-compounding is turned on. May differ
                               from active_strategy.compound_interval: a user can
                               enable compounding on Aggressive Alpha by setting
                               this to True even though the default is off.
        auto_execute:          If True, the dispatcher executes decisions without
                               asking for confirmation. If False, it sends a
                               proposal message and waits.
        current_position:      Dict of live position data (pool, token amounts,
                               tick range, token_id). None when not in a position.
        position_token_id:     The ERC-721 token ID minted by the position manager
                               when liquidity was added. None until first add.
        paused:                Set to True by /settings when the user pauses cycles.
        safety_locked:         Set to True by the safety controller on a critical
                               error. Cleared by clear_safety_lock() only.
        previous_snapshot:     The last complete market snapshot dict, used by
                               the analyser to compute per-cycle deltas.
        watchlist:             In-memory copy of the user's watchlist items
                               (mirrors the SQLite watchlist table).
        rebalance_count:       Lifetime count of rebalances. Shown in /dashboard.
        total_gas_spent_bnb:   Cumulative gas cost across all transactions (BNB).
        entry_value_usd:       USD value of tokens at the moment liquidity was
                               first added. Used for unrealised P&L calculation.
        scheduler_job:         Reference to the APScheduler Job object. Set by
                               core/scheduler.py in Sprint 10; None until then.
    """
    chat_id: int
    wallet_address: str
    private_key: str                          # RAM ONLY — never log
    active_strategy: StrategyProfile
    compound_enabled: bool
    auto_execute: bool

    # Position state — populated by executor.py
    current_position: Optional[Dict[str, Any]] = None
    position_token_id: Optional[int] = None

    # Cycle control flags
    paused: bool = False
    safety_locked: bool = False

    # Analysis data — updated each cycle
    previous_snapshot: Optional[Dict[str, Any]] = None

    # Watchlist in-memory cache — synced with SQLite on load/add/remove
    watchlist: List[Dict[str, Any]] = field(default_factory=list)

    # Lifetime performance metrics — updated by portfolio.py
    rebalance_count: int = 0
    total_gas_spent_bnb: float = 0.0
    entry_value_usd: float = 0.0

    # Scheduler handle — set by core/scheduler.py (Sprint 10)
    scheduler_job: Optional[Any] = None

    def has_position(self) -> bool:
        """Return True if the user currently holds an open LP position."""
        return self.current_position is not None and self.position_token_id is not None

    def is_operational(self) -> bool:
        """
        Return True if the scheduler should run a full cycle for this session.

        A session is non-operational if it has been paused by the user or
        locked by the safety controller after a critical error.
        """
        return not self.paused and not self.safety_locked

    def strategy_summary(self) -> str:
        """
        Return a one-line human-readable description of the current strategy.

        Used in /dashboard and the settings summary display.
        """
        s = self.active_strategy
        compound_str = "on" if self.compound_enabled else "off"
        exec_str = "auto" if self.auto_execute else "confirm"
        return (
            f"{s.name} | slippage ≤{s.max_slippage*100:.1f}% | "
            f"TVL ≥${s.min_tvl_usd:,.0f} | compound {compound_str} | {exec_str}"
        )


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Process-level registry mapping Telegram chat IDs to UserSession objects.

    This is a thin wrapper around a plain dict. It provides named methods
    so that callers read clearly — `session_manager.get(chat_id)` is more
    self-documenting than `_sessions[chat_id]`.

    Thread safety: python-telegram-bot v21 runs handlers in a single asyncio
    event loop. Since Python's dict operations are GIL-protected and we are
    not doing concurrent mutations across OS threads, a plain dict is safe
    here without extra locking.
    """

    def __init__(self) -> None:
        self._sessions: Dict[int, UserSession] = {}

    def get(self, chat_id: int) -> Optional[UserSession]:
        """
        Return the session for chat_id, or None if the user is not onboarded.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            UserSession if the user has completed onboarding, else None.
        """
        return self._sessions.get(chat_id)

    def create(self, session: UserSession) -> None:
        """
        Register a newly created session.

        Overwrites any existing session for the same chat_id (e.g., after
        the user runs /reset and then /start again).

        Args:
            session: The completed UserSession from onboarding.
        """
        self._sessions[session.chat_id] = session
        logger.info("Session created for chat_id %d, wallet %s", session.chat_id, session.wallet_address)

    def delete(self, chat_id: int) -> bool:
        """
        Remove and discard a session (called by /reset).

        The private key stored in the session is discarded along with the
        object — Python's garbage collector will free it from RAM.

        Args:
            chat_id: Telegram chat ID.

        Returns:
            True if a session existed and was removed, False if none existed.
        """
        if chat_id in self._sessions:
            wallet = self._sessions[chat_id].wallet_address
            del self._sessions[chat_id]
            logger.info("Session deleted for chat_id %d (wallet %s)", chat_id, wallet)
            return True
        return False

    def exists(self, chat_id: int) -> bool:
        """
        Return True if an active session exists for this chat_id.

        Args:
            chat_id: Telegram chat ID.
        """
        return chat_id in self._sessions

    def all_sessions(self) -> List[UserSession]:
        """
        Return a list of all active sessions.

        Used by the scheduler to iterate over users when running cycles.
        """
        return list(self._sessions.values())

    def count(self) -> int:
        """Return the number of currently active sessions."""
        return len(self._sessions)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

# This is the single instance shared across the entire application.
# Import it with:  from core.strategy_manager import session_manager
session_manager = SessionManager()
