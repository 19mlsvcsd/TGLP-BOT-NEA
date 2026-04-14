"""
bot/onboarding.py
=================
Multi-step onboarding conversation handler for the /start command.

This module implements the full wallet setup → strategy selection → preferences
→ confirmation flow that every new user must complete before the bot can manage
their liquidity. The flow is built as a python-telegram-bot ConversationHandler
so that each step receives only the relevant handler and invalid inputs are
rejected gracefully.

Conversation state machine:
    /start
      └─► AWAITING_KEY           (user sends private key)
            └─► AWAITING_STRATEGY      (user picks preset or custom)
                  ├─► [preset] ──────────► AWAITING_COMPOUND
                  └─► [custom]
                        └─► AWAITING_CUSTOM_PAIRS
                              └─► AWAITING_CUSTOM_TVL
                                    └─► AWAITING_CUSTOM_SLIPPAGE
                                          └─► AWAITING_CUSTOM_REBALANCE
                                                └─► AWAITING_CUSTOM_COMPOUND
                                                      └─► AWAITING_AUTOEXECUTE
                                                            └─► AWAITING_CONFIRM
                                                                  └─► [END]

Security:
- The private key message is deleted from the Telegram chat immediately after
  it is received, before any processing occurs.
- The private key is stored only in `context.user_data["ob"]["key"]` during
  the conversation, then moved to UserSession.private_key (RAM only) on
  completion. It is cleared from user_data immediately after session creation.
- It is never logged, never written to the database, and never appears in any
  error message sent to the user.
"""

import logging
from typing import Any, Dict

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

from config.settings import STRATEGY_PROFILES, StrategyConfig
from core.strategy_manager import UserSession, session_manager
from helpers.blockchain import get_bnb_balance, get_wallet_address, get_web3
from helpers.formatters import (
    escape_md,
    format_bnb,
    format_pct,
    format_strategy_summary,
    short_address,
)
from helpers.validators import (
    normalise_private_key,
    validate_positive_amount,
    validate_private_key,
    validate_slippage,
    validate_tvl_threshold,
)
from bot.keyboards import (
    autoexecute_keyboard,
    compound_toggle_keyboard,
    confirm_cancel_keyboard,
    custom_compound_interval_keyboard,
    custom_pairs_keyboard,
    main_menu_keyboard,
    strategy_selection_keyboard,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation state constants
# ---------------------------------------------------------------------------

(
    AWAITING_KEY,
    AWAITING_STRATEGY,
    AWAITING_CUSTOM_PAIRS,
    AWAITING_CUSTOM_TVL,
    AWAITING_CUSTOM_SLIPPAGE,
    AWAITING_CUSTOM_REBALANCE,
    AWAITING_CUSTOM_COMPOUND,
    AWAITING_COMPOUND,
    AWAITING_AUTOEXECUTE,
    AWAITING_CONFIRM,
) = range(10)


# ---------------------------------------------------------------------------
# Onboarding data helpers
# ---------------------------------------------------------------------------

def _ob(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """
    Return the onboarding scratch-pad from context.user_data.

    Creates the dict on first access so handlers never need to check
    whether it exists. Keyed with "ob" to avoid clashing with other
    user_data entries used elsewhere in the bot.
    """
    if "ob" not in context.user_data:
        context.user_data["ob"] = {}
    return context.user_data["ob"]


def _clear_ob(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Wipe the onboarding scratch-pad from context.user_data.

    Called as the final step of a successful onboarding or a cancellation
    to ensure the private key does not linger in user_data after the session
    is created.
    """
    if "ob" in context.user_data:
        # Explicitly zero-out the key entry before deleting.
        if "key" in context.user_data["ob"]:
            context.user_data["ob"]["key"] = ""
        del context.user_data["ob"]


# ---------------------------------------------------------------------------
# Step 0 — /start entry point
# ---------------------------------------------------------------------------

async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Entry point for the /start command.

    If the user already has an active session, sends a reminder message
    and ends the conversation immediately. Otherwise, sends the welcome
    message and asks for the private key.

    Returns:
        AWAITING_KEY to request the key, or ConversationHandler.END if
        the user is already onboarded.
    """
    chat_id = update.effective_chat.id

    if session_manager.exists(chat_id):
        await update.message.reply_text(
            "✅ You're already set up\\! Use /dashboard to check your position "
            "or /settings to change your preferences\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    welcome = (
        "*👋 Welcome to TGLP Bot*\n\n"
        "I automate liquidity pool management on PancakeSwap V3 \\(BSC Testnet\\)\\. "
        "I monitor pool data every 15 seconds, score opportunities against your chosen "
        "strategy, and can automatically allocate, rebalance, and compound your position\\.\n\n"
        "*What you need to get started:*\n"
        "• A BSC Testnet wallet private key\n"
        "• Some testnet BNB \\(free from the faucet\\)\n\n"
        "*Get testnet BNB:* [bnbchain\\.org/faucet\\-smart](https://testnet.bnbchain.org/faucet-smart)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔑 *Please send your BSC Testnet wallet private key\\.* \n\n"
        "_Your key will be deleted from the chat immediately after I read it\\. "
        "It is never stored on disk — only in memory while the bot runs\\._\n\n"
        "⚠️ *Only use a testnet wallet with test funds\\.* Never use a mainnet wallet here\\."
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN_V2)
    return AWAITING_KEY


# ---------------------------------------------------------------------------
# Step 1 — Receive and validate private key
# ---------------------------------------------------------------------------

async def receive_key(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive the private key message, delete it immediately, validate it,
    and proceed to strategy selection.

    The message is deleted BEFORE any further processing — even if the key
    turns out to be invalid. This ensures no key ever stays visible in chat.

    Returns:
        AWAITING_STRATEGY on success, AWAITING_KEY to ask again on failure.
    """
    message = update.message
    raw_key = message.text.strip() if message.text else ""

    # ── Security: delete the message immediately ──────────────────────────
    try:
        await context.bot.delete_message(
            chat_id=message.chat_id,
            message_id=message.message_id,
        )
    except Exception as e:
        # Deletion can fail if the bot lacks delete-messages permission in
        # the chat. Log the failure but continue — the key is still validated.
        logger.warning("Could not delete private key message: %s", e)
        await message.reply_text(
            "⚠️ I couldn't delete your key message\\. Please delete it manually "
            "and consider revoking this key\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    # ── Validate the key format ───────────────────────────────────────────
    valid, error = validate_private_key(raw_key)
    if not valid:
        await message.reply_text(
            f"❌ Invalid private key: {escape_md(error)}\n\n"
            "Please send your key again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AWAITING_KEY

    # ── Derive wallet address ─────────────────────────────────────────────
    normalised_key = normalise_private_key(raw_key)
    try:
        address = get_wallet_address(normalised_key)
    except Exception as e:
        logger.error("Failed to derive wallet address: %s", e)
        await message.reply_text(
            "❌ Could not derive a wallet address from that key\\. "
            "Please check it is a valid BSC private key and try again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AWAITING_KEY

    # ── Attempt to read BNB balance (best-effort) ─────────────────────────
    bnb_balance: float | None = None
    try:
        w3 = get_web3()
        bnb_balance = get_bnb_balance(w3, address)
    except Exception as e:
        logger.warning("Could not read BNB balance during onboarding: %s", e)

    # ── Store key and address in onboarding scratch-pad ───────────────────
    ob = _ob(context)
    ob["key"] = normalised_key          # Stored temporarily; cleared after session creation
    ob["address"] = address

    # ── Show wallet confirmation ──────────────────────────────────────────
    balance_line = (
        f"Balance: `{escape_md(format_bnb(bnb_balance))}`\n"
        if bnb_balance is not None
        else "Balance: _could not read — check your RPC connection_\n"
    )
    low_balance_warning = ""
    if bnb_balance is not None and bnb_balance < 0.01:
        low_balance_warning = (
            "\n⚠️ _Your balance is very low\\. You may need testnet BNB to pay gas\\._\n"
        )

    await message.reply_text(
        f"✅ *Wallet verified*\n\n"
        f"Address: `{escape_md(address)}`\n"
        f"{balance_line}"
        f"{low_balance_warning}\n"
        f"*Now choose a strategy:*\n\n"
        f"🛡 *Conservative Yield* — stablecoin pairs, low risk, auto\\-compound\n"
        f"⚖️ *Balanced Growth* — stablecoin \\+ large\\-cap pairs, moderate risk\n"
        f"🚀 *Aggressive Alpha* — large\\-cap pairs, highest APR potential\n"
        f"🔧 *Custom* — set your own parameters",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=strategy_selection_keyboard(),
    )
    return AWAITING_STRATEGY


# ---------------------------------------------------------------------------
# Step 2 — Receive strategy choice
# ---------------------------------------------------------------------------

async def receive_strategy(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle the strategy selection button press.

    For preset strategies: move straight to compound preference.
    For Custom: start the guided parameter flow.

    Returns:
        AWAITING_COMPOUND for presets, AWAITING_CUSTOM_PAIRS for custom.
    """
    query = update.callback_query
    await query.answer()
    choice = query.data.replace("ob_strat_", "")  # 'conservative', 'balanced', 'aggressive', 'custom'

    ob = _ob(context)
    ob["strategy_key"] = choice

    if choice == "custom":
        await query.edit_message_text(
            "*🔧 Custom Strategy Setup*\n\n"
            "Step 1 of 4: Which pair types should I target?",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=custom_pairs_keyboard(),
        )
        return AWAITING_CUSTOM_PAIRS

    # Preset strategy chosen — store it and move to compound preference.
    strategy = STRATEGY_PROFILES[choice]
    ob["strategy_obj"] = strategy

    compound_default = "on" if strategy.compound_interval else "off"
    await query.edit_message_text(
        f"*{escape_md(strategy.name)}* selected\\.\n\n"
        f"_Description:_ {escape_md(strategy.description)}\n\n"
        f"🔄 *Enable auto\\-compounding?*\n"
        f"Default for this strategy: *{compound_default}*",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=compound_toggle_keyboard(),
    )
    return AWAITING_COMPOUND


# ---------------------------------------------------------------------------
# Custom strategy flow (Steps 3a–3e)
# ---------------------------------------------------------------------------

async def receive_custom_pairs(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive the custom pair-type selection and ask for min TVL."""
    query = update.callback_query
    await query.answer()
    choice = query.data.replace("ob_pairs_", "")

    pair_map = {
        "stable":   ["stable-stable"],
        "mixed":    ["stable-stable", "stable-largecap"],
        "largecap": ["stable-largecap", "largecap-largecap"],
        "all":      ["stable-stable", "stable-largecap", "largecap-largecap"],
    }
    _ob(context)["custom_pairs"] = pair_map.get(choice, ["stable-stable"])

    await query.edit_message_text(
        "*🔧 Custom Strategy — Step 2 of 4*\n\n"
        "What is the *minimum TVL* \\(Total Value Locked\\) a pool must have for me "
        "to consider it?\n\n"
        "Enter a USD amount\\. Examples: `500000` \\($500K\\), `200000` \\($200K\\)\n\n"
        "_Recommended: at least \\$100,000 to ensure sufficient exit liquidity\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return AWAITING_CUSTOM_TVL


async def receive_custom_tvl(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive min TVL text input and ask for max slippage."""
    text = update.message.text.strip() if update.message.text else ""
    valid, error = validate_tvl_threshold(text)
    if not valid:
        await update.message.reply_text(
            f"❌ {escape_md(error)}\n\nPlease enter a valid TVL amount:",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AWAITING_CUSTOM_TVL

    _ob(context)["custom_tvl"] = float(text)

    await update.message.reply_text(
        "*🔧 Custom Strategy — Step 3 of 4*\n\n"
        "What is the *maximum slippage* you'll accept on trades?\n\n"
        "Enter a percentage\\. Examples: `0.5` \\(0\\.5%\\), `1.0` \\(1%\\)\n\n"
        "_Allowed range: 0\\.1% – 5\\.0%\\. Higher slippage risks front\\-running\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return AWAITING_CUSTOM_SLIPPAGE


async def receive_custom_slippage(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive max slippage text input and ask for rebalance threshold."""
    text = update.message.text.strip() if update.message.text else ""
    valid, error = validate_slippage(text)
    if not valid:
        await update.message.reply_text(
            f"❌ {escape_md(error)}\n\nPlease enter a valid slippage percentage:",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AWAITING_CUSTOM_SLIPPAGE

    _ob(context)["custom_slippage"] = float(text) / 100.0  # Store as fraction

    await update.message.reply_text(
        "*🔧 Custom Strategy — Step 4 of 4*\n\n"
        "What *rebalance threshold* should trigger a pool switch?\n\n"
        "Enter a percentage\\. Example: `15` means I'll rebalance if a better "
        "pool scores 15% higher than your current one\\.\n\n"
        "_Recommended: 10% – 25%\\. Lower \\= more frequent rebalancing\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return AWAITING_CUSTOM_REBALANCE


async def receive_custom_rebalance(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Receive rebalance threshold and ask for compound interval."""
    text = update.message.text.strip() if update.message.text else ""
    valid, error = validate_positive_amount(text)
    if not valid:
        await update.message.reply_text(
            f"❌ {escape_md(error)}\n\nEnter a threshold percentage like `15`:",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AWAITING_CUSTOM_REBALANCE

    threshold = float(text)
    if threshold < 1 or threshold > 50:
        await update.message.reply_text(
            "❌ Threshold must be between 1% and 50%\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return AWAITING_CUSTOM_REBALANCE

    _ob(context)["custom_rebalance"] = threshold / 100.0  # Store as fraction

    await update.message.reply_text(
        "*🔧 Auto\\-Compound Interval*\n\n"
        "How often should I collect and reinvest your earned fees?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=custom_compound_interval_keyboard(),
    )
    return AWAITING_CUSTOM_COMPOUND


async def receive_custom_compound(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive custom compound interval and assemble the StrategyConfig object.
    Then proceed to auto-execute preference.
    """
    query = update.callback_query
    await query.answer()
    raw = query.data.replace("ob_compound_", "")  # '1800', '3600', '14400', 'none'

    compound_interval = None if raw == "none" else int(raw)

    ob = _ob(context)
    # Build the custom StrategyConfig from all collected parameters.
    custom_strategy = StrategyConfig(
        name="Custom",
        description="User-defined strategy",
        allowed_pair_types=ob.get("custom_pairs", ["stable-stable"]),
        min_tvl_usd=ob.get("custom_tvl", 200_000),
        max_slippage=ob.get("custom_slippage", 0.005),
        rebalance_threshold=ob.get("custom_rebalance", 0.15),
        compound_interval=compound_interval,
        auto_execute=False,  # Will be set in the next step.
    )
    ob["strategy_obj"] = custom_strategy
    # compound_enabled mirrors whether an interval was chosen.
    ob["compound_enabled"] = compound_interval is not None

    await query.edit_message_text(
        "*⚡ Execution Mode*\n\n"
        "*Auto\\-execute:* I act immediately when my analysis says to move funds\\.\n\n"
        "*Confirm each trade:* I send you a proposal message and wait for your "
        "approval before touching anything\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=autoexecute_keyboard(),
    )
    return AWAITING_AUTOEXECUTE


# ---------------------------------------------------------------------------
# Compound preference (preset strategies only)
# ---------------------------------------------------------------------------

async def receive_compound_pref(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive the Yes/No compounding choice for preset strategies and move
    to the auto-execute preference step.
    """
    query = update.callback_query
    await query.answer()
    compound_enabled = query.data == "ob_comp_yes"
    _ob(context)["compound_enabled"] = compound_enabled

    await query.edit_message_text(
        "*⚡ Execution Mode*\n\n"
        "*Auto\\-execute:* I act immediately when my analysis says to move funds\\.\n\n"
        "*Confirm each trade:* I send you a proposal message and wait for your "
        "approval before touching anything\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=autoexecute_keyboard(),
    )
    return AWAITING_AUTOEXECUTE


# ---------------------------------------------------------------------------
# Auto-execute preference
# ---------------------------------------------------------------------------

async def receive_autoexecute(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive the auto-execute choice and show the final confirmation summary.
    """
    query = update.callback_query
    await query.answer()
    auto_execute = query.data == "ob_exec_auto"

    ob = _ob(context)
    ob["auto_execute"] = auto_execute
    strategy = ob["strategy_obj"]
    compound_enabled = ob.get("compound_enabled", bool(strategy.compound_interval))
    address = ob["address"]

    # Read current BNB balance for the summary (best-effort).
    bnb_balance = None
    try:
        w3 = get_web3()
        bnb_balance = get_bnb_balance(w3, address)
    except Exception:
        pass

    summary = format_strategy_summary(
        strategy=strategy,
        compound_enabled=compound_enabled,
        auto_execute=auto_execute,
        wallet_address=address,
        bnb_balance=bnb_balance,
    )
    await query.edit_message_text(
        summary,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=confirm_cancel_keyboard(),
    )
    return AWAITING_CONFIRM


# ---------------------------------------------------------------------------
# Final confirmation
# ---------------------------------------------------------------------------

async def receive_final_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle the Confirm button: create the UserSession and end the conversation.

    This is the only place where UserSession is created. After creation, the
    private key is cleared from context.user_data immediately.
    """
    query = update.callback_query
    await query.answer()

    if query.data == "ob_final_cancel":
        _clear_ob(context)
        await query.edit_message_text(
            "❌ Setup cancelled\\. Run /start whenever you're ready to try again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    # Build the session from scratch-pad data.
    ob = _ob(context)
    strategy = ob["strategy_obj"]
    compound_enabled = ob.get("compound_enabled", bool(strategy.compound_interval))

    session = UserSession(
        chat_id=update.effective_chat.id,
        wallet_address=ob["address"],
        private_key=ob["key"],        # Transferred to session RAM; cleared below.
        active_strategy=strategy,
        compound_enabled=compound_enabled,
        auto_execute=ob.get("auto_execute", False),
    )
    session_manager.create(session)

    # ── Clear private key from user_data immediately ──────────────────────
    _clear_ob(context)
    # ── Done ──────────────────────────────────────────────────────────────

    logger.info(
        "Onboarding complete for chat_id %d, strategy: %s",
        session.chat_id, strategy.name,
    )

    # TODO Sprint 10: start_scheduler(session, w3, notifier_callback)

    await query.edit_message_text(
        f"🎉 *All set\\!*\n\n"
        f"Wallet: `{escape_md(short_address(session.wallet_address))}`\n"
        f"Strategy: *{escape_md(strategy.name)}*\n\n"
        f"The bot is ready\\. Use the menu below to explore pools, "
        f"allocate funds, or check your dashboard\\.\n\n"
        f"_The automated scheduler will start once you run_ /allocate _for the first time\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel / fallback
# ---------------------------------------------------------------------------

async def cancel_onboarding(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle /cancel during onboarding — wipes scratch-pad and ends conversation.
    """
    _clear_ob(context)
    await update.message.reply_text(
        "❌ Onboarding cancelled\\. Run /start to begin again\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler assembly
# ---------------------------------------------------------------------------

onboarding_handler = ConversationHandler(
    # The conversation starts when the user sends /start.
    entry_points=[CommandHandler("start", start_command)],

    states={
        AWAITING_KEY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_key)
        ],
        AWAITING_STRATEGY: [
            CallbackQueryHandler(receive_strategy, pattern="^ob_strat_")
        ],
        AWAITING_CUSTOM_PAIRS: [
            CallbackQueryHandler(receive_custom_pairs, pattern="^ob_pairs_")
        ],
        AWAITING_CUSTOM_TVL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_tvl)
        ],
        AWAITING_CUSTOM_SLIPPAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_slippage)
        ],
        AWAITING_CUSTOM_REBALANCE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_rebalance)
        ],
        AWAITING_CUSTOM_COMPOUND: [
            CallbackQueryHandler(receive_custom_compound, pattern="^ob_compound_")
        ],
        AWAITING_COMPOUND: [
            CallbackQueryHandler(receive_compound_pref, pattern="^ob_comp_")
        ],
        AWAITING_AUTOEXECUTE: [
            CallbackQueryHandler(receive_autoexecute, pattern="^ob_exec_")
        ],
        AWAITING_CONFIRM: [
            CallbackQueryHandler(receive_final_confirm, pattern="^ob_final_")
        ],
    },

    fallbacks=[CommandHandler("cancel", cancel_onboarding)],

    # Allow the conversation to restart with a fresh /start even if one is
    # already in progress (e.g., user restarted and wants to change wallet).
    allow_reentry=True,

    # Store conversation state per user (not per chat) so it works in groups.
    per_user=True,
    per_chat=False,

    # Explicitly set per_message=False: CallbackQueryHandlers here track
    # conversation state per-user, not per-message. This silences PTBUserWarning.
    per_message=False,
)
