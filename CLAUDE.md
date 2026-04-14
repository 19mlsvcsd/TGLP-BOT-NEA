# CLAUDE.md — TGLP Bot Project Log

This file is a living progress log for the TGLP Bot NEA project.
Every Claude session must read this file first to understand the current
project state before making any changes.

**Project:** TGLP Bot — Telegram Liquidity Pool Manager  
**Platform:** BSC Testnet / PancakeSwap V3  
**Language:** Python 3.11+  
**Purpose:** OCR A Level Computer Science NEA  

---

## Sprint 1 — Project Skeleton & Configuration — 2026-04-14

### Completed
- Created the full project directory structure: `config/`, `core/`, `bot/`, `helpers/`, `tests/`, `config/abi/`
- Created `__init__.py` files for all packages: `config`, `core`, `bot`, `helpers`, `tests`
- Wrote `requirements.txt` with all five pinned dependencies
- Wrote `.env.example` with placeholders for `TELEGRAM_BOT_TOKEN` and `BSC_TESTNET_RPC_URL`
- Wrote `.gitignore` covering `.env`, `__pycache__/`, `*.db`, `.vscode/`, `venv/`
- Implemented `config/settings.py`: all constants, PancakeSwap V3 addresses, strategy profile dataclasses (Conservative Yield, Balanced Growth, Aggressive Alpha), scoring weights, anomaly thresholds, gas limits, API URLs, token lists
- Implemented `helpers/validators.py`: validate_private_key, validate_ethereum_address, validate_positive_amount, validate_slippage, validate_tvl_threshold, validate_apr_threshold, normalise_private_key, normalise_address — all with (bool, str) return tuples
- Implemented `helpers/database.py`: SQLite setup with `logs`, `trades`, and `watchlist` tables; CRUD helpers for all three tables; WAL journal mode; row_factory for dict access
- Implemented `main.py`: dotenv loading, logging configuration, startup banner, env check (exits if TELEGRAM_BOT_TOKEN missing), database initialisation, run_bot() call
- Created `bot/app.py` stub: placeholder start_bot() that prints Sprint 1 complete message and blocks — allows main.py imports to succeed before Sprint 3

### Files Created/Modified
- `main.py` — entry point: dotenv, logging, DB init, bot startup
- `requirements.txt` — pinned dependencies
- `.env.example` — environment variable template
- `.gitignore` — excludes .env, bytecode, DB files, venv
- `CLAUDE.md` — this file
- `config/__init__.py` — package marker
- `config/settings.py` — all constants and strategy profile definitions
- `config/abi/` — directory created, ABI files will be added in Sprints 2 and 7
- `core/__init__.py` — package marker
- `bot/__init__.py` — package marker
- `bot/app.py` — stub for Sprint 3
- `helpers/__init__.py` — package marker
- `helpers/validators.py` — full input validation library
- `helpers/database.py` — full SQLite layer with CRUD helpers
- `tests/__init__.py` — package marker

### Tested
- `main.py` runs: startup banner prints, env check runs, database initialises, stub message appears
- `helpers/validators.py`: validate_private_key accepts 64-char hex and 0x-prefixed variants, rejects short/invalid inputs; validate_ethereum_address accepts checksummed addresses, rejects non-hex; validate_slippage rejects <0.1% and >5%; validate_positive_amount rejects zero and negative
- `helpers/database.py`: initialise_database() creates all three tables; insert_log, insert_trade, insert_watchlist_item all return valid IDs; get_trades_for_user returns empty list for new users

### Current State
- Project skeleton is complete with all directories and package files in place
- Configuration layer (settings.py) is fully populated — no magic numbers in any other module
- Validation layer (validators.py) is fully implemented
- Database layer (database.py) is fully implemented with all three tables
- The bot does NOT yet connect to Telegram — that is Sprint 3
- The bot does NOT yet connect to the blockchain — that is Sprint 2

### Next Sprint
- Sprint 2: Blockchain Connection Layer
  - `helpers/blockchain.py`: Web3 connection, wallet derivation, BNB/token balance reads, gas estimation, transaction building/signing/broadcasting, ERC-20 approval helper
  - `config/abi/erc20.json`: standard ERC-20 ABI
  - Test: connect to BSC Testnet, verify chain ID 97, read a BNB balance

### Notes
- The `eth_utils` library is a dependency of `web3` and is available without a separate install. `validate_ethereum_address` and `normalise_address` use it.
- `config/settings.py` uses `int | None` union type syntax (Python 3.10+). Ensure Python 3.10+ is used.
- Strategy profiles are defined as `StrategyConfig` dataclass instances in settings.py and imported by `core/strategy_manager.py` in Sprint 3.
- DeFiLlama returns mainnet data only. For testnet development, mainnet pool data is used for discovery/scoring and testnet for execution. This is a documented known limitation.
- The `.env` file must be created manually by copying `.env.example` — it is gitignored and will never be committed.
