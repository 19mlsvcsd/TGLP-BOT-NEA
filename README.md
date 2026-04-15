# TGLP Bot: Telegram Liquidity Pool Manager

**OCR A Level Computer Science NEA Project**

A Telegram bot that monitors PancakeSwap V3 liquidity pools on the BSC Testnet, analyses market conditions, and proposes (or automatically executes) liquidity allocation decisions on behalf of the user.

---

## Features

- **Onboarding**: Secure `/start` flow; the wallet private key is accepted, used to derive the public address, then discarded from memory. Never stored on disk.
- **Pool discovery**: Fetches live pool data from DeFiLlama's yields API and filters it by the user's chosen strategy.
- **Delta analysis**: Compares consecutive market snapshots to detect significant APR/TVL changes and anomalous data points.
- **Decision engine**: Scores pools using a weighted formula (APR 40%, TVL 30%, stability 20%, volume 10%) and recommends ALLOCATE, REBALANCE, COMPOUND, or NO_ACTION.
- **Safety controller**: Blocks execution if gas is too high, wallet balance is insufficient, or repeated anomalies are detected (auto safety lock after 3 consecutive anomalous cycles).
- **Auto-execute or propose**: Users can choose whether the bot executes decisions automatically or sends a proposal for manual confirmation.
- **Portfolio tracking**: Tracks entry value, unrealised P&L, gas costs, and rebalance history.
- **Watchlist alerts**: Monitor specific pools for APR/TVL threshold breaches.
- **Scheduled cycles**: Analysis runs automatically every `CYCLE_INTERVAL_SECONDS` (default: 300 s) for each onboarded user.

---

## Project Structure

```
TGLP-BOT-NEA/
├── main.py                    # Entry point: loads env, starts bot
├── requirements.txt           # Pinned dependencies
├── .env.example               # Environment variable template
├── config/
│   ├── settings.py            # All constants, strategy profiles, token lists
│   └── abi/                   # Contract ABIs (ERC-20, Factory, Router, PositionManager, Pool)
├── core/
│   ├── market_data.py         # DeFiLlama + Binance price fetching, pool snapshot
│   ├── analyser.py            # Per-cycle delta analysis and anomaly detection
│   ├── decision_engine.py     # Pool filtering, scoring, and decision logic
│   ├── executor.py            # On-chain execution: swap, add/remove liquidity, compound
│   ├── safety.py              # Pre-execution safety checks and anomaly escalation
│   ├── portfolio.py           # Position valuation, P&L calculation
│   ├── watchlist.py           # Watchlist CRUD (session + SQLite bridge)
│   ├── alerts.py              # Threshold alert checking
│   ├── scheduler.py           # APScheduler wrapper for per-user cycle jobs
│   ├── dispatcher.py          # Full analysis → decision → execution pipeline
│   └── strategy_manager.py   # UserSession dataclass and SessionManager singleton
├── bot/
│   ├── app.py                 # Application setup, lifecycle hooks, handler registration
│   ├── onboarding.py          # /start ConversationHandler (10 states)
│   ├── commands.py            # All command handlers (/dashboard, /allocate, etc.)
│   ├── callbacks.py           # Inline keyboard callback router
│   ├── conversations.py       # /watch ConversationHandler
│   └── keyboards.py           # All InlineKeyboardMarkup builders
├── helpers/
│   ├── blockchain.py          # Web3 connection, balance reads, gas, tx signing
│   ├── database.py            # SQLite setup and CRUD helpers
│   ├── formatters.py          # Telegram MarkdownV2 formatting utilities
│   └── validators.py          # Input validation (private key, address, amounts)
└── tests/
    ├── test_validators.py     # helpers/validators.py: 37 tests
    ├── test_analyser.py       # core/analyser.py: 16 tests
    ├── test_decision_engine.py# core/decision_engine.py: 16 tests
    ├── test_portfolio.py      # core/portfolio.py: 17 tests
    ├── test_safety.py         # core/safety.py: 20 tests (19 unit + 1 live)
    └── test_sprint12.py       # End-to-end integration: 18 tests (17 unit + 1 live)
```

---

## Prerequisites

- Python 3.11+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- BSC Testnet RPC URL (public endpoint or private node)
- A BSC Testnet wallet with a small amount of test BNB for gas

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd TGLP-BOT-NEA
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=your_token_here
BSC_TESTNET_RPC_URL=https://data-seed-prebsc-1-s1.binance.org:8545/
```

> **Note:** The `.env` file is gitignored and must never be committed.

### 3. Run the bot

```bash
python main.py
```

The bot will:
1. Load environment variables
2. Initialise the SQLite database (`tglp_bot.db`)
3. Connect to BSC Testnet
4. Start polling for Telegram messages
5. Register a scheduler job for each user who completes onboarding

---

## Usage

### First-time setup

1. Open a chat with your bot on Telegram
2. Send `/start`
3. Follow the onboarding flow:
   - Enter your BSC Testnet private key (the message is deleted immediately)
   - Choose a strategy (Conservative Yield, Balanced Growth, or Aggressive Alpha)
   - Set compound and auto-execute preferences
4. The bot will confirm your wallet address, BNB balance, and selected strategy

### Commands

| Command | Description |
|---|---|
| `/start` | Begin onboarding or restart the setup flow |
| `/dashboard` | Portfolio overview: position value, P&L, gas spent, system health |
| `/allocate` | Trigger an immediate analysis and allocation cycle |
| `/explore` | Browse top pools filtered by your strategy |
| `/alerts` | Manage your watchlist and APR/TVL alert thresholds |
| `/history` | View your trade history with pagination |
| `/export` | Export all trades as text |
| `/settings` | Toggle compound, auto-execute, and pause; change strategy |
| `/watch` | Add a new watchlist alert for a pool or token |
| `/reset` | Remove your session and wallet data |
| `/help` | Full command reference and DeFi concept explanations |

### Strategies

| Strategy | Pair types | Min TVL | Rebalance threshold |
|---|---|---|---|
| Conservative Yield | stable-stable only | $500,000 | 0.20 |
| Balanced Growth | stable-stable, stable-largecap | $100,000 | 0.15 |
| Aggressive Alpha | all pair types | $50,000 | 0.10 |

---

## Running Tests

### Unit tests (no network required)

```bash
python tests/test_validators.py
python tests/test_analyser.py
python tests/test_decision_engine.py
python tests/test_portfolio.py
python tests/test_safety.py      # (skips the live gas-price check)
python tests/test_sprint12.py    # (skips the live pipeline test)
```

### Live tests (requires BSC Testnet connectivity)

```bash
python tests/test_safety.py      # test_check_gas_price_live
python tests/test_sprint12.py    # test_live_full_pipeline
```

---

## Architecture Notes

### Security

- Private keys exist **only in RAM** inside `UserSession.private_key`. They are never written to disk, never logged, and never appear in exception messages.
- The onboarding handler deletes the key message from Telegram immediately and clears the key from `user_data` after session creation.
- All transactions include `chainId: 97` (BSC Testnet) to prevent replay attacks on mainnet.

### asyncio / APScheduler bridge

The Telegram bot runs on an asyncio event loop. APScheduler's `BackgroundScheduler` runs on a separate thread. The `notify_func` closure captures the event loop reference at startup and uses `asyncio.run_coroutine_threadsafe` to safely dispatch Telegram messages from the scheduler thread.

### Data flow

```
DeFiLlama API ─┐
Binance API    ─┤─► market_data.py ─► analyser.py ─► decision_engine.py
BSC Testnet    ─┘                                         │
                                                          │
                                              executor.py ◄── safety.py
                                                          │
                                              portfolio.py + alerts.py
                                                          │
                                              dispatcher.py (ties it all together)
                                                          │
                                              scheduler.py ─► notify_func ─► Telegram
```

### Database

SQLite (`tglp_bot.db`) stores three tables:
- `logs`: bot event log (INFO/WARNING/ERROR entries)
- `trades`: executed trade records (action type, pool, tx hash, gas cost, status)
- `watchlist`: user-defined APR/TVL threshold alerts (soft-deleted with `active=0`)

Session state (position details, P&L accumulators, strategy) is held **in memory only** and is reset if the bot process restarts. Users must re-run `/start` after a restart.

---

## Known Limitations

- **Testnet data**: DeFiLlama returns mainnet pool data. Pool addresses from DeFiLlama are mainnet addresses and cannot be used for on-chain execution on BSC Testnet. The decision engine uses mainnet data for discovery and scoring; execution uses testnet for safety during development.
- **Token pricing**: Unknown tokens (anything other than USDT, USDC, BUSD, DAI, WBNB, BNB) are priced at $0. This prevents P&L overstatement but undercounts value when exotic tokens are held.
- **In-memory session**: If the bot process restarts, all user sessions are lost. Re-onboarding is required. This is intentional; private keys must not be stored on disk.
- **Single-hop swaps**: `execute_allocate` uses single-hop V3 swaps via the Router. Multi-hop routing (e.g., TOKEN → WBNB → USDT) is not implemented.
- **Slippage**: `amountOutMinimum = 0` in swap calls. This is intentional for testnet shallow liquidity pools. Mainnet deployment would require oracle-derived minimum amounts.
- **No persistence across restarts**: The `consecutive_anomalies` counter in `SafetyController` and all session state resets on process restart.
