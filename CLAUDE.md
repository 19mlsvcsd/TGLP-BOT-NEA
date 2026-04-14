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

---

## Sprint 2 — Blockchain Connection Layer — 2026-04-14

### Completed
- Implemented `helpers/blockchain.py`: full Web3 connection layer with all required functions
- Created `config/abi/erc20.json`: standard ERC-20 ABI (balanceOf, approve, allowance, transfer, transferFrom, name, symbol, decimals, totalSupply + Transfer/Approval events)

### Files Created/Modified
- `helpers/blockchain.py` — Web3 connection, wallet derivation, BNB/token balance reads, gas estimation, tx building, simulation via eth_call, sign+broadcast+receipt, ERC-20 approval with allowance pre-check, RPC latency measurement
- `config/abi/erc20.json` — standard ERC-20 ABI

### Tested
- `get_web3()` — connects to BSC Testnet, verifies chain ID 97, reads current block number (101,663,765)
- `get_rpc_latency_ms()` — measures RPC round-trip (~79 ms on test run)
- `get_wallet_address()` — derives correct address from well-known test private key (Hardhat account #0)
- `get_bnb_balance()` — reads balance of PancakeSwap factory address (0.0 BNB, expected for a contract)
- `get_gas_price_gwei()` — reads current gas price (0.10 Gwei on testnet)
- `simulate_transaction()` — correctly returns False for a transaction with an invalid function selector

### Current State
- Blockchain connection layer is fully implemented
- Bot can connect to BSC Testnet, derive wallet addresses, read BNB balances, estimate gas, build/simulate/sign/broadcast transactions, and handle ERC-20 approvals
- No Telegram bot yet — that is Sprint 3

### Next Sprint
- Sprint 3: Telegram Bot Core & Onboarding
  - `core/strategy_manager.py`: StrategyProfile dataclass, UserSession class, SessionManager
  - `helpers/formatters.py`: Telegram message formatting helpers
  - `bot/keyboards.py`: all inline keyboard layouts
  - `bot/onboarding.py`: full /start ConversationHandler (wallet → strategy → prefs → confirm)
  - `bot/callbacks.py`, `bot/commands.py` (all commands, /help fully implemented)
  - `bot/app.py`: full Application setup replacing the Sprint 1 stub

### Notes
- `sign_and_send()` uses `signed.raw_transaction` (web3.py v7 attribute name, not `rawTransaction`).
- `estimate_gas()` applies a 20% buffer over the raw estimate to reduce out-of-gas failures.
- All transactions include `chainId: 97` to prevent replay attacks on mainnet.
- `simulate_transaction()` must be called before every `sign_and_send()` in executor.py — enforced by design, not just convention.

---

## Sprint 3 — Telegram Bot Core & Onboarding — 2026-04-14

### Completed
- Implemented `core/strategy_manager.py`: `StrategyProfile` (alias of `StrategyConfig`), `UserSession` dataclass with `has_position()` / `is_operational()` helpers, `SessionManager` class with full CRUD, `session_manager` module-level singleton
- Implemented `helpers/formatters.py`: `escape_md` (MarkdownV2 escaping), `format_bnb/usd/pct/large_usd/token_amount`, `format_timedelta_short`, `format_timestamp`, `short_address`, `tx_hash_link`, `format_pool_info`, `format_tx_summary`, `format_strategy_summary`
- Implemented `bot/keyboards.py`: 12 keyboard functions covering strategy selection, custom pair/compound selection, compound toggle, auto-execute choice, confirm/cancel, main menu, settings panel (with live state labels), pool explorer + detail, history pagination, watchlist, action confirmation, reset confirmation
- Implemented `bot/onboarding.py`: full 10-state ConversationHandler for `/start` — private key receive+immediate delete, wallet derivation, BNB balance read, preset and custom strategy flows, compound preference, auto-execute preference, final confirmation, session creation with key cleared from `user_data` immediately after
- Implemented `bot/commands.py`: stub handlers for all 10 commands + fully implemented `/help` with per-command descriptions and DeFi concept explainers (`/help lp`, `/help apr`, `/help tvl`, `/help il`, `/help v3`)
- Implemented `bot/callbacks.py`: full callback router (`handle_callback`) dispatching to sub-handlers for menu, settings toggles (compound/auto-execute/pause are live), pool explorer, history, watchlist, action confirmation, and reset
- Implemented `bot/conversations.py`: `/watch` ConversationHandler stub (3-state skeleton) + `custom_strategy_handler` placeholder for Sprint 11
- Replaced `bot/app.py` Sprint 1 stub with full `Application` setup, handler registration in correct priority order, `run_polling(drop_pending_updates=True)`
- Created `tests/test_sprint3.py`: 5 test functions covering all Sprint 3 modules

### Files Created/Modified
- `core/strategy_manager.py` — UserSession dataclass, SessionManager, singleton
- `helpers/formatters.py` — full Telegram message formatting library
- `bot/keyboards.py` — all 12 inline keyboard layout functions
- `bot/onboarding.py` — full /start ConversationHandler (10 states)
- `bot/commands.py` — all command stubs + complete /help
- `bot/callbacks.py` — callback router + live settings toggles
- `bot/conversations.py` — /watch stub + custom_strategy placeholder
- `bot/app.py` — full Application setup (replaced Sprint 1 stub)
- `tests/test_sprint3.py` — Sprint 3 test suite

### Tested
- `core/strategy_manager.py`: UserSession paused/safety_locked flags, SessionManager CRUD, singleton identity
- `helpers/formatters.py`: escape_md covers all MarkdownV2 special chars, number formatters, timedelta, address shortening, format_strategy_summary content
- `bot/keyboards.py`: structure, callback_data values, pagination nav, settings label toggling
- All bot modules import cleanly; ConversationHandler has 10 states
- PTBUserWarning about `per_message=False` is expected/informational — not an error

### Current State
- Telegram bot is fully runnable: `python main.py` connects to Telegram, shows onboarding on `/start`, allows strategy selection, creates a `UserSession` in memory
- Private key security: message deleted immediately, key cleared from `user_data` after session creation, never persisted
- Settings toggles (compound, auto-execute, pause) are live even in Sprint 3
- All other commands return informative stub messages pointing to the sprint that will implement them

### Next Sprint
- Sprint 4: complete — see entry below.

---

## Sprint 4 — Market Data & Pool Discovery — 2026-04-14

### Completed
- Created `config/abi/pancake_pool_v3.json`: minimal pool ABI (slot0, liquidity, fee, token0, token1, tickSpacing) — full ABI added in Sprint 7
- Implemented `core/market_data.py`:
  - `PoolData` dataclass: all pool fields from API + on-chain sources
  - `MarketSnapshot` dataclass: pool list + prices + warnings, with `top_pools()` and `get_pool()` helpers
  - `fetch_defi_llama_pools()`: fetches and filters DeFiLlama yields API
  - `fetch_token_prices()`: fetches BNB/ETH/BTC/CAKE from Binance public API
  - `fetch_on_chain_pool_data(w3, address)`: reads slot0, liquidity, fee from pool contract
  - `classify_pool_pair(symbol)`: classifies pair as stable-stable / stable-largecap / largecap-largecap / other
  - `_is_pool_valid(raw)`: rejects zero-TVL, negative APR, missing fields
  - `build_pool_snapshot(raw_pools, prices, w3, enrich_on_chain)`: assembles validated PoolData list sorted by APR
  - `get_market_snapshot(...)`: cached entry point (30s TTL), handles all API failures gracefully
  - `invalidate_cache()`: forces fresh fetch on next call
- Created `tests/test_sprint4.py`: 7 tests (4 unit + 3 live API)

### Files Created/Modified
- `config/abi/pancake_pool_v3.json` — pool ABI for slot0/liquidity/fee reads
- `core/market_data.py` — full market data module
- `tests/test_sprint4.py` — Sprint 4 test suite

### Tested
- `classify_pool_pair()` — all 4 pair types, edge cases (empty string, unknown pairs)
- `_is_pool_valid()` — rejects zero TVL, negative TVL, negative APR, empty address
- `build_pool_snapshot()` — 2 valid pools + 2 rejected, correct APR sort, fee tier conversion (DeFiLlama fraction → bps)
- `MarketSnapshot` — pool count, `top_pools()`, `get_pool()` lookup
- Live DeFiLlama: 38 PancakeSwap BSC pools fetched; top pool MBOX-WBNB at 15.20% APR
- Live Binance: BNB $614.82, ETH $2,317.48, BTC $74,005.90
- Live `get_market_snapshot()`: 38 pools assembled in one call; cache returns same object on second call

### Current State
- Market data layer is fully functional with live mainnet pool data
- `get_market_snapshot()` is ready for the analyser (Sprint 5) and decision engine (Sprint 6) to consume

### Next Sprint
- Sprint 5: complete — see entry below.

---

## Sprint 5 — Analysis & Delta Engine — 2026-04-14

### Completed
- Implemented `core/analyser.py`:
  - `PoolDelta` dataclass: per-pool change metrics (APR abs/pct, TVL abs/pct, volume pct, anomaly flags)
  - `AnalysisResult` dataclass: full cycle output with `get_delta()` and `clean_pools()` helpers
  - `analyse_cycle(current, previous)`: handles first-run, computes deltas for matched pools, counts new/dropped pools
  - `detect_anomalies(delta)`: checks APR spike (>50% relative) and TVL drop (>30% relative)
  - `get_pool_stability_score(address, delta_history)`: mean-abs-APR-change normalised to [0,1]; neutral 0.5 for no history
  - `check_price_deviation(current, previous)`: on-chain sqrtPriceX96 comparison, >10% in one cycle = anomaly
  - `significant_change` flag: set when any clean pool's APR moves ≥0.5pp
- Created `tests/test_sprint5.py`: 10 tests

### Files Created/Modified
- `core/analyser.py` — full delta analysis module
- `tests/test_sprint5.py` — Sprint 5 test suite

### Tested
- `analyse_cycle()` first run, stable cycle, significant change, new/dropped pool counting
- APR spike and TVL drop anomaly detection (unit + integration)
- `clean_pools()` correctly filters anomalous pools from a list
- `get_pool_stability_score()`: neutral, stable, volatile, extreme, and address-filtered cases
- `check_price_deviation()`: no on-chain data, large move, small move
- Live two-cycle test with 38 real pools — 38 compared, correct significant_change=False

### Current State
- Full data pipeline is working: market_data → analyser → ready for decision_engine
- Anomaly detection works on live data; near-zero APR pools (RUSD-BUSD etc.) correctly trigger spike flags on large relative nudges

### Next Sprint
- Sprint 6: Decision Engine
  - `core/decision_engine.py`: Decision enum, DecisionResult dataclass, `filter_pools_by_strategy()`, `score_pools()`, `make_decision()`, `format_decision_summary()`

### Notes
- `significant_change=False` even with live anomalies — correct, because anomalous pools are excluded from the significance check
- Near-zero APR pools will frequently trigger the relative-spike anomaly on any non-trivial data refresh; this is correct and expected behaviour
- `_SIGNIFICANCE_APR_CHANGE_PP = 0.5` is the threshold for triggering decision engine re-evaluation; can be tuned in analyser.py if needed
- `_STABILITY_NORMALISER_PP = 10.0` means a pool averaging 10pp APR change per cycle scores 0.0 stability

### Notes
- **Critical discovery**: DeFiLlama uses `"pancakeswap-amm"` (not `"pancakeswap-amm-v3"`) for BSC pools. The V3 slug only covers Base and Ethereum chains. Both slugs are now accepted by the filter.
- DeFiLlama fee tiers are stored as fractions (e.g., `0.0001` = 0.01%); on-chain values are in bps (`100` = 0.01%). `build_pool_snapshot()` converts fractions to bps.
- `enrich_on_chain=False` by default — on-chain enrichment adds ~0.1s per pool and is only used for `/explore`.
- Cache TTL is 30s (SNAPSHOT_CACHE_SECONDS in settings.py). The dispatcher invalidates the cache before a manual `/allocate` cycle.

### Notes
- `format_strategy_summary` MarkdownV2-escapes all values including BNB amounts (`.` → `\.`) — tests must check for escaped form
- `history_keyboard()` returns `InlineKeyboardMarkup` with `.inline_keyboard` as a tuple, not a list — compare with `len()` not `== []`
- The `per_message=False` PTBUserWarning on `ConversationHandler` instantiation is unavoidable without `per_message=True` — accepted as informational
- `session_manager` singleton is in `core/strategy_manager.py` — import with `from core.strategy_manager import session_manager`
- `bot/app.py` uses `drop_pending_updates=True` so stale messages from restarts are discarded

---

## Sprint 6 — Decision Engine — 2026-04-14

### Completed
- Implemented `core/decision_engine.py`:
  - `Decision` enum: NO_ACTION, ALLOCATE, REBALANCE, COMPOUND
  - `ScoredPool` dataclass: PoolData + composite score + four normalised component scores + `score_breakdown()` helper
  - `DecisionResult` dataclass: action, target pool, current pool, reasoning, scored_pools list, gas estimates, pool counts
  - `filter_pools_by_strategy(pools, strategy, analysis_result)`: pair type filter → TVL floor filter → anomaly exclusion filter
  - `score_pools(pools, delta_history)`: min-max normalisation per metric, 0.5 for tied metrics, weighted composite (0.40 APR + 0.30 TVL + 0.20 stability + 0.10 volume), sorted descending
  - `make_decision(scored_pools, current_position, strategy, analysis_result, compound_enabled, fees_available, pools_filtered_count)`: 5-case decision tree
  - `format_decision_summary(result)`: MarkdownV2 Telegram message with action icon, reasoning, target pool details, and gas estimate
  - `_estimate_gas(action)`: gas unit and BNB cost estimates using conservative 5 Gwei reference price
- Created `tests/test_sprint6.py`: 18 tests (17 unit + 1 live API)

### Files Created/Modified
- `core/decision_engine.py` — full decision engine
- `tests/test_sprint6.py` — Sprint 6 test suite

### Tested
- `filter_pools_by_strategy()` — pair type filtering, TVL floor, anomaly exclusion, no analysis_result (first run)
- `score_pools()` — single pool (all 0.5), ranking order, empty input, identical pools (all 0.5)
- `make_decision()` — all 5 outcomes: no pools (NO_ACTION), no position (ALLOCATE), better pool (REBALANCE), compound (COMPOUND), stable (NO_ACTION); threshold not met (NO_ACTION); current pool anomalous/filtered (forced REBALANCE)
- `format_decision_summary()` — smoke test all four Decision values
- `_estimate_gas()` — correct unit counts and BNB costs for all actions
- Live pipeline: 38 pools fetched, 5 passed BALANCED_GROWTH filter, top pool CAKE-USDT (score: 0.590), decision: ALLOCATE

### Current State
- Full analysis-to-decision pipeline is working: market_data → analyser → decision_engine
- Decision engine is ready to be called by the dispatcher (Sprint 10) and executor (Sprint 7)

### Next Sprint
- Sprint 7: Execution Engine
  - `core/executor.py`: full execution layer — swap, add liquidity, remove liquidity, collect fees, compound
  - `config/abi/pancake_router_v3.json`: PancakeSwap V3 Router ABI
  - `config/abi/pancake_factory_v3.json`: PancakeSwap V3 Factory ABI
  - `config/abi/pancake_position_manager.json`: NonfungiblePositionManager ABI

### Notes
- **Min-max normalisation edge case**: With only two pools, even tiny APR/TVL differences normalise to [0, 1] and produce a large score gap. Tests for threshold-not-met behaviour require a third "anchor" pool with clearly inferior values to keep the gap small. This is expected and correct behaviour — the normalisation is relative to the pool set being scored.
- `_GAS_PRICE_REFERENCE_GWEI = 5.0` is a conservative BSC mainnet figure. Actual testnet gas will be ~0.1 Gwei; the reference is used only for the user-facing gas cost display.
- `rebalance_threshold` in StrategyConfig is a raw score gap (not a percentage). Conservative (0.20), Balanced (0.15), Aggressive (0.10).
- The decision engine never calls the blockchain. It is purely analytical. `executor.py` (Sprint 7) handles all on-chain interactions.

---

## Sprint 7 — Execution Engine — 2026-04-14

### Completed
- Created `config/abi/pancake_factory_v3.json`: V3 Factory ABI — `getPool(token0, token1, fee) → address`
- Created `config/abi/pancake_router_v3.json`: V3 Router ABI — `exactInputSingle(params) → amountOut`, `refundETH()`
- Created `config/abi/pancake_position_manager.json`: NonfungiblePositionManager ABI — `mint`, `increaseLiquidity`, `decreaseLiquidity`, `collect`, `positions`, `balanceOf`, `tokenOfOwnerByIndex`, `ownerOf`, `burn`
- Implemented `core/executor.py`:
  - `ExecutionResult` dataclass: success, action, tx_hashes, token_id, amounts, fees, gas used/cost, error
  - `_round_tick(tick, tick_spacing)`: floor-divides tick to nearest multiple — handles negative ticks correctly
  - `_apply_slippage(amount_wei, slippage)`: integer floor of amount × (1 − slippage)
  - `_deadline()`: TX_DEADLINE_OFFSET seconds from now
  - `_get_token_balance_raw(w3, token, wallet)`: raw ERC-20 balanceOf in smallest unit (not human-readable float)
  - `_get_pool_tokens_and_tick(w3, pool)`: reads token0/token1/fee/tickSpacing/currentTick from pool contract
  - `get_position(w3, token_id)`: read position data dict; None on failure
  - `get_user_positions(w3, wallet)`: list of token IDs; empty list on failure
  - `get_pool_address(w3, token0, token1, fee)`: factory lookup; None if pool doesn't exist
  - `collect_fees(w3, token_id, wallet, key)`: collect(MAX_UINT128) for both tokens
  - `remove_liquidity(w3, token_id, wallet, key, slippage)`: decreaseLiquidity + collect (skips decrease if liquidity=0)
  - `swap_exact_input_single(w3, token_in, token_out, fee, amount_wei, recipient, key, slippage, bnb_value)`: single-hop V3 swap; amountOutMinimum=0 (testnet)
  - `add_liquidity(w3, token0, token1, fee, amount0, amount1, tick_lower, tick_upper, wallet, key, slippage, bnb_value)`: mint new LP position
  - `execute_allocate(w3, pool_data, amount_bnb, wallet, key, strategy)`: full allocate — BNB balance check → on-chain pool read → tick range → swap → approve → mint
  - `execute_rebalance(w3, token_id, new_pool, amount_bnb, wallet, key, strategy)`: remove_liquidity + execute_allocate
  - `execute_compound(w3, token_id, pool_data, wallet, key, strategy)`: collect_fees + increaseLiquidity
- Created `tests/test_sprint7.py`: 14 tests across three tiers (unit / read-only on-chain / execution failure)

### Files Created/Modified
- `config/abi/pancake_factory_v3.json` — V3 Factory ABI
- `config/abi/pancake_router_v3.json` — V3 Router ABI
- `config/abi/pancake_position_manager.json` — NonfungiblePositionManager ABI
- `core/executor.py` — full execution engine
- `tests/test_sprint7.py` — Sprint 7 test suite

### Tested
- Unit: ExecutionResult defaults, _round_tick (positive/negative/exact), _apply_slippage (floor behaviour), _deadline (future timestamp), WBNB_ADDRESS format, all ABI files load with correct function names, tick range logic
- Read-only on-chain: get_position(0) → None, get_user_positions(burn address) → [], get_pool_address(fake tokens) → None, all three contracts instantiate correctly
- Execution safety: collect_fees with no funds fails via simulation rejection, execute_allocate fails on BNB balance pre-flight check, remove_liquidity with non-existent token_id returns None from get_position and errors cleanly

### Current State
- Execution engine is fully implemented and handles all three action types
- All write functions call simulate_transaction() before sign_and_send() without exception
- Read functions (get_position, get_user_positions, get_pool_address) handle RPC failures gracefully
- Real LP transactions require funded testnet wallet — not tested end-to-end (by design)

### Next Sprint
- Sprint 8: Safety Controller
  - `core/safety.py`: gas price guard, position size limits, emergency pause, safety lock, anomaly threshold escalation

### Notes
- **web3.py v7 API**: Use `contract.encode_abi("functionName", args=[params])` (snake_case). The v6 `contract.encodeABI(fn_name=...)` method does not exist in v7.6.0.
- `_get_token_balance_raw()` returns raw ERC-20 units (not human-readable). Use this when passing amounts to contract calls. `get_token_balance()` in blockchain.py returns a float divided by 10**decimals — wrong for contract input.
- Tick range: executor uses ±10 × tickSpacing around currentTick. This is wide enough to capture price movement on testnet while remaining a bounded range. Adjust the multiplier in `execute_allocate` for mainnet use.
- `amountOutMinimum = 0` in swap_exact_input_single is intentional for testnet shallow liquidity. Set this to an oracle-derived minimum on mainnet.
- `execute_compound` uses `increaseLiquidity` (same position, same NFT token ID) — not burn+remint. This preserves the position's fee accrual history.
