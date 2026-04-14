"""
bot/keyboards.py
================
Inline keyboard layouts for TGLP Bot.

Every InlineKeyboardMarkup used anywhere in the bot is defined here as a
function that returns a fresh keyboard object. Centralising keyboards means
button labels and callback data strings are defined in one place — if a
callback pattern changes, only this file needs updating.

Callback data naming convention:
    <scope>_<action>[_<parameter>]

Scopes used in this file:
    ob_       — onboarding conversation
    cmd_      — top-level command responses
    cfg_      — settings/config changes
    pool_     — pool explorer actions
    hist_     — history pagination
    watch_    — watchlist management
    alert_    — alert management
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ---------------------------------------------------------------------------
# Onboarding keyboards
# ---------------------------------------------------------------------------

def strategy_selection_keyboard() -> InlineKeyboardMarkup:
    """
    Four-button keyboard for choosing a strategy profile during onboarding.

    Callback data values match the keys in config/settings.STRATEGY_PROFILES
    plus 'custom' for the user-defined flow.
    """
    keyboard = [
        [
            InlineKeyboardButton(
                "🛡 Conservative Yield",
                callback_data="ob_strat_conservative",
            )
        ],
        [
            InlineKeyboardButton(
                "⚖️ Balanced Growth",
                callback_data="ob_strat_balanced",
            )
        ],
        [
            InlineKeyboardButton(
                "🚀 Aggressive Alpha",
                callback_data="ob_strat_aggressive",
            )
        ],
        [
            InlineKeyboardButton(
                "🔧 Custom Strategy",
                callback_data="ob_strat_custom",
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def custom_pairs_keyboard() -> InlineKeyboardMarkup:
    """
    Keyboard for choosing which pair types the custom strategy targets.

    The values map to the pair_type strings used by market_data.classify_pool_pair().
    """
    keyboard = [
        [InlineKeyboardButton("💵 Stable–Stable only",       callback_data="ob_pairs_stable")],
        [InlineKeyboardButton("📈 Stable + Large-Cap",        callback_data="ob_pairs_mixed")],
        [InlineKeyboardButton("💎 Large-Cap only",            callback_data="ob_pairs_largecap")],
        [InlineKeyboardButton("🌐 All pairs",                 callback_data="ob_pairs_all")],
    ]
    return InlineKeyboardMarkup(keyboard)


def custom_compound_interval_keyboard() -> InlineKeyboardMarkup:
    """
    Keyboard for choosing the compound interval during custom strategy setup.

    The integer values (seconds) are stored directly in callback_data so the
    handler can parse them without a lookup table.
    """
    keyboard = [
        [
            InlineKeyboardButton("Every 30 min",  callback_data="ob_compound_1800"),
            InlineKeyboardButton("Every hour",    callback_data="ob_compound_3600"),
        ],
        [
            InlineKeyboardButton("Every 4 hours", callback_data="ob_compound_14400"),
            InlineKeyboardButton("Disabled",      callback_data="ob_compound_none"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def compound_toggle_keyboard() -> InlineKeyboardMarkup:
    """
    Yes/No keyboard for the auto-compounding preference step in onboarding.
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, enable compounding", callback_data="ob_comp_yes"),
            InlineKeyboardButton("❌ No thanks",               callback_data="ob_comp_no"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def autoexecute_keyboard() -> InlineKeyboardMarkup:
    """
    Keyboard for choosing execution mode during onboarding.

    Auto: the bot executes decisions immediately when the cycle fires.
    Confirm: the bot sends a proposal and waits for the user to tap Confirm.
    """
    keyboard = [
        [InlineKeyboardButton("⚡ Auto-execute",      callback_data="ob_exec_auto")],
        [InlineKeyboardButton("🔔 Confirm each trade", callback_data="ob_exec_confirm")],
    ]
    return InlineKeyboardMarkup(keyboard)


def confirm_cancel_keyboard() -> InlineKeyboardMarkup:
    """
    Generic confirm/cancel keyboard used for the final onboarding summary,
    /reset confirmation, and any destructive action confirmation.
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="ob_final_confirm"),
            InlineKeyboardButton("❌ Cancel",  callback_data="ob_final_cancel"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Main menu keyboard
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Quick-access menu sent after onboarding and by the /menu command.

    Provides one-tap access to the most frequently used commands.
    """
    keyboard = [
        [
            InlineKeyboardButton("📊 Dashboard",  callback_data="cmd_dashboard"),
            InlineKeyboardButton("🔍 Explore",    callback_data="cmd_explore"),
        ],
        [
            InlineKeyboardButton("⚡ Allocate",   callback_data="cmd_allocate"),
            InlineKeyboardButton("📜 History",    callback_data="cmd_history"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings",   callback_data="cmd_settings"),
            InlineKeyboardButton("❓ Help",       callback_data="cmd_help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Settings menu keyboard
# ---------------------------------------------------------------------------

def settings_menu_keyboard(
    compound_enabled: bool,
    auto_execute: bool,
) -> InlineKeyboardMarkup:
    """
    Settings panel with toggleable options.

    Shows current state inline on each button so the user can see what is
    active without reading a separate status message.

    Args:
        compound_enabled: Current compounding state — shown on the button label.
        auto_execute:     Current execution mode — shown on the button label.
    """
    compound_label = (
        "🔄 Compounding: ON" if compound_enabled else "🔄 Compounding: OFF"
    )
    exec_label = (
        "⚡ Execution: Auto" if auto_execute else "⚡ Execution: Confirm"
    )
    keyboard = [
        [InlineKeyboardButton("📋 Change Strategy",   callback_data="cfg_change_strategy")],
        [InlineKeyboardButton(compound_label,          callback_data="cfg_toggle_compound")],
        [InlineKeyboardButton(exec_label,              callback_data="cfg_toggle_autoexec")],
        [InlineKeyboardButton("📉 Change Slippage",   callback_data="cfg_change_slippage")],
        [InlineKeyboardButton("⏸ Pause / Resume",     callback_data="cfg_toggle_pause")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Pool explorer keyboards
# ---------------------------------------------------------------------------

def pool_list_keyboard(pools: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard for a page of pool results from /explore.

    Each pool gets a button labelled with its rank and pair symbol. Tapping
    the button sends the pool address as callback data so the handler can
    look up full details.

    Args:
        pools:       List of pool dicts for the current page (up to 5 items).
        page:        Current 0-based page index.
        total_pages: Total number of pages.

    Returns:
        InlineKeyboardMarkup with pool buttons plus Prev/Next navigation.
    """
    offset = page * 5  # each page shows 5 pools
    pool_buttons = []
    for i, pool in enumerate(pools):
        label = f"#{offset + i + 1} {pool.get('symbol', 'Unknown')}"
        addr = pool.get("pool", "")
        pool_buttons.append(
            [InlineKeyboardButton(label, callback_data=f"pool_detail_{addr[:20]}")]
        )

    nav_buttons = _pagination_row(page, total_pages, prefix="pool_page")
    if nav_buttons:
        pool_buttons.append(nav_buttons)

    return InlineKeyboardMarkup(pool_buttons)


def pool_detail_keyboard(pool_address: str) -> InlineKeyboardMarkup:
    """
    Action keyboard shown when the user taps a pool in /explore.

    Args:
        pool_address: Full pool contract address (used in callback data).
    """
    # Truncate address in callback data to stay within Telegram's 64-char limit.
    addr_key = pool_address[:20]
    keyboard = [
        [
            InlineKeyboardButton("👁 Watch this pool",    callback_data=f"watch_add_{addr_key}"),
            InlineKeyboardButton("⬅️ Back to list",       callback_data="pool_back_list"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# History pagination
# ---------------------------------------------------------------------------

def history_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """
    Pagination keyboard for the /history command.

    Args:
        page:        Current 0-based page index.
        total_pages: Total number of history pages.
    """
    nav = _pagination_row(page, total_pages, prefix="hist_page")
    if nav:
        return InlineKeyboardMarkup([nav])
    return InlineKeyboardMarkup([])


# ---------------------------------------------------------------------------
# Watchlist / alerts keyboards
# ---------------------------------------------------------------------------

def watchlist_keyboard(watch_items: list) -> InlineKeyboardMarkup:
    """
    Display active watchlist items with a Remove button for each.

    Args:
        watch_items: List of watchlist item dicts from database.py.
    """
    keyboard = []
    for item in watch_items:
        label = f"🗑 Remove: {item.get('identifier', '?')} ({item.get('threshold_type', '?')})"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"alert_remove_{item['id']}")]
        )
    if not keyboard:
        keyboard.append(
            [InlineKeyboardButton("No active alerts", callback_data="alert_noop")]
        )
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Execution confirmation keyboard
# ---------------------------------------------------------------------------

def action_confirm_keyboard(action_id: str) -> InlineKeyboardMarkup:
    """
    Confirm/Cancel keyboard for proposed LP actions when auto_execute is False.

    The action_id is embedded in callback_data so the handler knows which
    pending action to execute or discard.

    Args:
        action_id: Short unique identifier for the pending action (e.g., a
                   timestamp string or sequential integer as a string).
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ Execute",  callback_data=f"action_confirm_{action_id}"),
            InlineKeyboardButton("❌ Skip",     callback_data=f"action_skip_{action_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Reset confirmation keyboard
# ---------------------------------------------------------------------------

def reset_confirm_keyboard() -> InlineKeyboardMarkup:
    """
    Confirmation keyboard for the destructive /reset command.

    Uses distinct callback_data from ob_final_confirm to prevent accidental
    cross-handler triggering.
    """
    keyboard = [
        [
            InlineKeyboardButton("⚠️ Yes, reset everything", callback_data="reset_confirm_yes"),
            InlineKeyboardButton("❌ Cancel",                  callback_data="reset_confirm_no"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pagination_row(page: int, total_pages: int, prefix: str) -> list:
    """
    Build a Prev/Next button row for paginated displays.

    Args:
        page:        Current 0-based page index.
        total_pages: Total number of pages.
        prefix:      Callback data prefix (e.g., 'hist_page', 'pool_page').

    Returns:
        List of InlineKeyboardButton objects for this row.
        Empty list if there is only one page.
    """
    if total_pages <= 1:
        return []

    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}_{page - 1}")
        )
    # Page counter in the middle (disabled button used as a label).
    buttons.append(
        InlineKeyboardButton(
            f"{page + 1}/{total_pages}", callback_data="page_noop"
        )
    )
    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_{page + 1}")
        )
    return buttons
