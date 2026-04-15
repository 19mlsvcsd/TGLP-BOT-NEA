"""
helpers/formatters.py
=====================
Telegram message formatting utilities for TGLP Bot.

All user-visible text produced by the bot passes through functions in this
module. Centralising formatting here keeps business logic modules clean and
makes it easy to change the presentation layer without touching core logic.

Key responsibilities:
- Escape text for Telegram's MarkdownV2 parse mode (required to avoid
  Telegram rejecting messages that contain special characters).
- Format numbers (BNB amounts, USD values, percentages) consistently.
- Build composite message strings for pools, transactions, and strategy
  summaries that are ready to send directly to Telegram.

Telegram MarkdownV2 special characters that must be escaped with a backslash:
  _ * [ ] ( ) ~ ` > # + - = | { } . !
"""

import re
from datetime import datetime
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# MarkdownV2 escaping
# ---------------------------------------------------------------------------

# Characters that must be backslash-escaped in Telegram MarkdownV2 messages.
# The regex matches any of these characters anywhere in the string.
_MD2_ESCAPE_PATTERN = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')


def escape_md(text: Any) -> str:
    """
    Escape a string for safe use in a Telegram MarkdownV2 message.

    Any character in the MarkdownV2 special-character set is prefixed with
    a backslash. Call this on all user-supplied data and dynamic values
    before inserting them into a formatted message.

    Args:
        text: Value to escape. Non-string values are converted with str().

    Returns:
        Escaped string safe for MarkdownV2 parsing.

    Examples:
        escape_md("hello_world")   → "hello\\_world"
        escape_md("3.14")          → "3\\.14"
        escape_md("0xAb12 (BSC)")  → "0xAb12 \\(BSC\\)"
    """
    return _MD2_ESCAPE_PATTERN.sub(r'\\\1', str(text))


# ---------------------------------------------------------------------------
# Number formatters
# ---------------------------------------------------------------------------

def format_bnb(amount: float) -> str:
    """
    Format a BNB amount to 4 decimal places with the BNB suffix.

    Args:
        amount: BNB value as a float.

    Returns:
        Formatted string, e.g. "0.1234 BNB".
    """
    return f"{amount:.4f} BNB"


def format_usd(amount: float) -> str:
    """
    Format a USD value with thousands separator and 2 decimal places.

    Args:
        amount: USD value as a float.

    Returns:
        Formatted string, e.g. "$1,234.56".
    """
    return f"${amount:,.2f}"


def format_pct(value: float, decimals: int = 2) -> str:
    """
    Format a percentage value.

    Args:
        value:    The percentage as a float, e.g. 5.25 for 5.25%.
        decimals: Number of decimal places (default 2).

    Returns:
        Formatted string, e.g. "5.25%".
    """
    return f"{value:.{decimals}f}%"


def format_large_usd(amount: float) -> str:
    """
    Format a large USD value using K/M/B suffixes for readability.

    Used for TVL figures in pool listings where full precision is unnecessary.

    Args:
        amount: USD value as a float.

    Returns:
        Compact string, e.g. "$1.23M", "$456.7K", "$2.1B".
    """
    if abs(amount) >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if abs(amount) >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:.2f}"


def format_token_amount(amount: float, symbol: str, decimals: int = 4) -> str:
    """
    Format a token amount with its symbol.

    Args:
        amount:   Token balance as a float (already divided by 10^decimals).
        symbol:   Token ticker, e.g. "USDT".
        decimals: Number of decimal places to show.

    Returns:
        Formatted string, e.g. "100.0000 USDT".
    """
    return f"{amount:.{decimals}f} {symbol}"


# ---------------------------------------------------------------------------
# Timestamp formatter
# ---------------------------------------------------------------------------

def format_timestamp(ts: str) -> str:
    """
    Convert an ISO-8601 UTC timestamp string to a readable local format.

    The database stores timestamps as ISO strings produced by
    datetime.utcnow().isoformat(timespec='seconds'). This function renders
    them as "14 Apr 2026, 17:30 UTC" for display in trade history.

    Args:
        ts: ISO timestamp string, e.g. "2026-04-14T17:30:00".

    Returns:
        Human-readable string. Returns the original string on parse failure.
    """
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%-d %b %Y, %H:%M UTC")
    except (ValueError, TypeError):
        # Fallback: return raw string rather than crashing.
        return str(ts)


def format_timedelta_short(seconds: int) -> str:
    """
    Format a number of seconds as a concise human duration.

    Used for displaying compound intervals (e.g., "30 min", "1 h").

    Args:
        seconds: Duration in seconds.

    Returns:
        Short string, e.g. "30 min", "1 h", "4 h", "1 d".
    """
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} h"
    days = seconds // 86400
    return f"{days} d"


# ---------------------------------------------------------------------------
# Address formatter
# ---------------------------------------------------------------------------

def short_address(address: str) -> str:
    """
    Shorten a 42-character address to the first 6 and last 4 characters.

    Args:
        address: Full checksummed address, e.g. "0xAb5801a...92d3".

    Returns:
        Shortened form, e.g. "0xAb58...92d3".
    """
    if len(address) >= 10:
        return f"{address[:6]}...{address[-4:]}"
    return address


def tx_hash_link(tx_hash: str) -> str:
    """
    Build a Telegram inline URL for a transaction hash pointing to BSCScan Testnet.

    Returns a MarkdownV2-formatted link, already escaped and ready to embed
    in a message that uses parse_mode=MarkdownV2.

    Args:
        tx_hash: Full 66-character transaction hash string.

    Returns:
        MarkdownV2 link string, e.g. "[0xab12...3f4e](https://testnet.bscscan.com/tx/0x...)".
    """
    from config.settings import BSCSCAN_TESTNET_URL
    short = short_address(tx_hash)
    url = f"{BSCSCAN_TESTNET_URL}{tx_hash}"
    # Escape the displayed text but NOT the URL (Telegram handles URL chars).
    return f"[{escape_md(short)}]({url})"


# ---------------------------------------------------------------------------
# Pool info formatter
# ---------------------------------------------------------------------------

def format_pool_info(pool: Dict[str, Any], rank: Optional[int] = None) -> str:
    """
    Format a pool data dict as a readable MarkdownV2 Telegram message block.

    Designed for use in /explore listings. Each block shows the essential
    metrics a user needs to evaluate a pool at a glance.

    Args:
        pool: Pool dict with keys: symbol, apr, tvl_usd, volume_24h,
              fee_tier, pool (address), pair_type.
        rank: Optional 1-based rank number to prefix the block (e.g. "#1").

    Returns:
        MarkdownV2-formatted string ready to send. Does NOT end with a newline.
    """
    symbol = escape_md(pool.get("symbol", "Unknown"))
    apr = pool.get("apr", 0.0)
    tvl = pool.get("tvl_usd", 0.0)
    volume = pool.get("volume_24h", 0.0)
    fee_tier = pool.get("fee_tier", "N/A")
    address = pool.get("pool", "")
    pair_type = escape_md(pool.get("pair_type", "unknown"))

    rank_str = f"*\\#{rank}* " if rank is not None else ""
    addr_short = escape_md(short_address(address)) if address else "N/A"

    lines = [
        f"{rank_str}*{symbol}*",
        f"APR: `{escape_md(format_pct(apr))}` \\| TVL: `{escape_md(format_large_usd(tvl))}`",
        f"24h Vol: `{escape_md(format_large_usd(volume))}` \\| Fee: `{escape_md(str(fee_tier))}`",
        f"Type: {pair_type} \\| `{addr_short}`",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Transaction summary formatter
# ---------------------------------------------------------------------------

def format_tx_summary(trade: Dict[str, Any]) -> str:
    """
    Format a trade record from the database as a single MarkdownV2 message line.

    Used in /history to show a compact per-trade entry with a BSCScan link.

    Args:
        trade: Dict matching the trades table schema (from database.py).

    Returns:
        MarkdownV2 string for one trade entry.
    """
    action = escape_md(trade.get("action_type", "N/A").replace("_", " ").title())
    ts = format_timestamp(trade.get("timestamp", ""))
    status = trade.get("status", "pending")
    status_icon = "✅" if status == "confirmed" else ("❌" if status == "failed" else "⏳")

    token_in = escape_md(trade.get("token_in") or "N/A")
    token_out = escape_md(trade.get("token_out") or "N/A")
    amount_in = escape_md(trade.get("amount_in") or "N/A")

    tx_hash = trade.get("tx_hash")
    hash_str = tx_hash_link(tx_hash) if tx_hash else "pending"

    gas_bnb = trade.get("gas_cost_bnb")
    gas_str = f"`{escape_md(gas_bnb)} BNB`" if gas_bnb else "N/A"

    return (
        f"{status_icon} *{action}* on {escape_md(ts)}\n"
        f"  {token_in} → {token_out} \\(amt: `{amount_in}`\\)\n"
        f"  TX: {hash_str} \\| Gas: {gas_str}"
    )


# ---------------------------------------------------------------------------
# Strategy summary formatter
# ---------------------------------------------------------------------------

def format_strategy_summary(
    strategy,
    compound_enabled: bool,
    auto_execute: bool,
    wallet_address: str,
    bnb_balance: Optional[float] = None,
) -> str:
    """
    Format the strategy confirmation screen shown at the end of onboarding.

    This is the last thing the user sees before confirming their setup.
    It summarises every parameter so the user can verify before committing.

    Args:
        strategy:        StrategyConfig instance (built-in or custom).
        compound_enabled: Whether auto-compound is enabled.
        auto_execute:    Whether decisions execute automatically.
        wallet_address:  User's wallet address (shown shortened).
        bnb_balance:     Current BNB balance (shown if available).

    Returns:
        MarkdownV2-formatted confirmation message string.
    """
    pairs_str = escape_md(", ".join(strategy.allowed_pair_types))
    slippage_str = escape_md(format_pct(strategy.max_slippage * 100))
    tvl_str = escape_md(format_large_usd(strategy.min_tvl_usd))
    rebalance_str = escape_md(format_pct(strategy.rebalance_threshold * 100))
    compound_str = (
        escape_md(format_timedelta_short(strategy.compound_interval))
        if compound_enabled and strategy.compound_interval
        else "Off"
    )
    exec_str = "Auto" if auto_execute else "Confirm each"
    balance_line = (
        f"Balance: `{escape_md(format_bnb(bnb_balance))}`\n"
        if bnb_balance is not None
        else ""
    )

    return (
        f"*📋 Setup Summary*\n\n"
        f"Wallet: `{escape_md(short_address(wallet_address))}`\n"
        f"{balance_line}"
        f"\n"
        f"*Strategy:* {escape_md(strategy.name)}\n"
        f"Pairs: {pairs_str}\n"
        f"Min TVL: {tvl_str}\n"
        f"Max slippage: {slippage_str}\n"
        f"Rebalance threshold: {rebalance_str}\n"
        f"Auto\\-compound: {compound_str}\n"
        f"Execution: {exec_str}\n\n"
        f"Confirm these settings?"
    )
