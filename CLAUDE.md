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
- Sprint 8: complete — see entry below.

### Notes
- **web3.py v7 API**: Use `contract.encode_abi("functionName", args=[params])` (snake_case). The v6 `contract.encodeABI(fn_name=...)` method does not exist in v7.6.0.
- `_get_token_balance_raw()` returns raw ERC-20 units (not human-readable). Use this when passing amounts to contract calls. `get_token_balance()` in blockchain.py returns a float divided by 10**decimals — wrong for contract input.
- Tick range: executor uses ±10 × tickSpacing around currentTick. This is wide enough to capture price movement on testnet while remaining a bounded range. Adjust the multiplier in `execute_allocate` for mainnet use.
- `amountOutMinimum = 0` in swap_exact_input_single is intentional for testnet shallow liquidity. Set this to an oracle-derived minimum on mainnet.
- `execute_compound` uses `increaseLiquidity` (same position, same NFT token ID) — not burn+remint. This preserves the position's fee accrual history.

---

## Sprint 8 — Safety Controller — 2026-04-15

### Completed
- Added three new constants to `config/settings.py`:
  - `MAX_GAS_PRICE_GWEI = 20.0` — hard execution ceiling (double the warning level)
  - `MAX_POSITION_FRACTION = 0.90` — maximum fraction of wallet per LP position
  - `SAFETY_ANOMALY_LOCK_THRESHOLD = 3` — consecutive anomaly cycles before safety lock
- Implemented `core/safety.py`:
  - `SafetyCheckResult` dataclass: `passed`, `check_name`, `reason` (empty string when passing)
  - `SafetyController` class:
    - `check_gas_price(w3, max_gwei)`: reads live gas price, blocks if above `MAX_GAS_PRICE_GWEI`
    - `check_position_size(amount_bnb, wallet_balance_bnb, max_fraction)`: blocks if allocation exceeds `MAX_POSITION_FRACTION` of wallet; also handles zero-balance wallet
    - `check_gas_reserve(wallet_balance_bnb, amount_bnb)`: blocks if remaining balance after allocation < `MIN_BNB_FOR_GAS`
    - `check_session_state(session)`: blocks if session is safety-locked or paused
    - `run_pre_execution_checks(w3, session, amount_bnb, wallet_balance_bnb)`: runs all four checks in order (cheapest first); returns first failure or passed; accepts optional `wallet_balance_bnb` override for testing
    - `trigger_emergency_pause(session, reason)`: sets `session.safety_locked = True`, logs at ERROR level
    - `clear_safety_lock(session)`: clears `session.safety_locked = False`
    - `record_anomaly_cycle(session, has_anomalies)`: increments per-user consecutive anomaly counter; resets on clean cycle; calls `trigger_emergency_pause` when threshold reached
    - `reset_anomaly_counter(chat_id)`: manually resets counter (called on safety lock clear)
    - `get_system_health(w3)`: returns dict with `connected`, `chain_id`, `block_number`, `gas_price_gwei`, `rpc_latency_ms`, `safe_to_trade`
  - `safety_controller` module-level singleton
- Created `tests/test_sprint8.py`: 16 tests across two tiers

### Files Created/Modified
- `config/settings.py` — 3 new safety constants
- `core/safety.py` — full safety controller
- `tests/test_sprint8.py` — Sprint 8 test suite

### Tested
- Unit: SafetyCheckResult defaults, position_size pass/fail (including zero balance edge case), gas_reserve pass/fail, session_state operational/paused/locked, trigger_emergency_pause, clear_safety_lock, anomaly escalation to lock on 3rd cycle, anomaly counter reset on clean cycle
- Read-only on-chain: check_gas_price on live testnet (0.100 Gwei, well below 20 Gwei limit passes), get_system_health (block 101770626, latency ~78 ms, safe_to_trade=True), run_pre_execution_checks with paused session (blocked at session_state), run_pre_execution_checks with wallet_balance_bnb=0.0 (blocked at gas_reserve)

### Current State
- Safety controller is fully implemented
- `run_pre_execution_checks` is ready to be called by the dispatcher before every execution cycle
- Anomaly escalation integrates with `record_anomaly_cycle` — dispatcher calls this after each `analyse_cycle`
- `get_system_health` is ready for the /status command (Sprint 11)

### Next Sprint
- Sprint 9: complete — see entry below.

### Notes
- `run_pre_execution_checks` check order: session_state → gas_price → gas_reserve → position_size. Session and gas checks come before balance reads so the cheapest failures are caught first.
- `wallet_balance_bnb` optional parameter on `run_pre_execution_checks` allows the dispatcher to pass a pre-read balance (saving one RPC call) or tests to inject a specific value without needing a funded address.
- Precompile address `0x000...001` has non-zero BNB on BSC Testnet — do not use it as a "zero balance" test address. Pass `wallet_balance_bnb=0.0` directly instead.
- `_consecutive_anomalies` dict is keyed by `chat_id` (int). The counter persists for the lifetime of the process; `reset_anomaly_counter` should be called whenever the user clears a safety lock.

---

## Sprint 9 — Portfolio & Watchlist — 2026-04-15

### Completed
- Implemented `core/portfolio.py`:
  - `PositionValue` dataclass: amount0/amount1 (human-readable), token symbols, value_usd, value_bnb, bnb_price_used
  - `PnLResult` dataclass: entry_value_usd, current_value_usd, unrealised P&L (USD + %), gas_spent_bnb, gas_cost_usd, net_pnl_usd, rebalance_count
  - `PortfolioSummary` dataclass: has_position, position_value, pnl, wallet_bnb, wallet_usd
  - `_token_usd_value(amount, symbol, bnb_price)`: stablecoins→$1, WBNB/BNB→×price, unknown→$0 (conservative)
  - `estimate_position_value(position_dict, bnb_price_usd)`: converts token amounts to USD using conservative pricing
  - `calculate_pnl(session, current_value_usd, bnb_price_usd)`: full P&L breakdown; 0% change when entry=0
  - `record_entry_value(session, value_usd)`: sets session.entry_value_usd (called by dispatcher after allocate)
  - `record_gas_cost(session, gas_cost_bnb)`: accumulates session.total_gas_spent_bnb
  - `build_portfolio_summary(w3, session, bnb_price_usd)`: one live RPC call for wallet balance; all other data from session
- Implemented `core/watchlist.py`:
  - `MAX_WATCHLIST_ITEMS = 20` cap to bound alert-checking loop
  - `load_watchlist(session)`: replaces session.watchlist from SQLite
  - `add_watch_item(session, item_type, identifier, threshold_type, threshold_value)`: writes to DB then appends to in-memory list; returns watch_id or -1 if full/failed
  - `remove_watch_item(session, watch_id)`: soft-deletes from DB (active=0) then removes from in-memory list; user_chat_id check prevents cross-user deletion
  - `get_watch_item(session, watch_id)`: in-memory lookup by ID; None if not found
  - `count_watch_items(session)`: len(session.watchlist)
- Implemented `core/alerts.py`:
  - `Alert` dataclass: watch_id, chat_id, item_type, identifier, threshold_type, threshold_value, current_value, message
  - `_check_pool_item(item, pool_data)`: internal helper — checks apr_above / apr_below / tvl_below for one item; returns Alert or None; unknown threshold_type is logged and skipped
  - `check_pool_alerts(session, snapshot)`: iterates session.watchlist for pool items; silently skips pools not in snapshot
  - `check_all_alerts(session, snapshot, prices)`: delegates to check_pool_alerts; prices dict accepted for future token price alerts
  - `format_alert_message(alert)`: prepends "[ALERT] " to alert.message
- Created `tests/test_sprint9.py`: 20 tests across two tiers

### Files Created/Modified
- `core/portfolio.py` — portfolio valuation and P&L module
- `core/watchlist.py` — watchlist session/DB bridge
- `core/alerts.py` — watchlist alert checking
- `tests/test_sprint9.py` — Sprint 9 test suite

### Tested
- Portfolio: stablecoin pair value=$800, WBNB+USDT value=$900, unknown tokens=$0, P&L profit/loss/zero, record_entry_value, record_gas_cost accumulation
- Watchlist: add item (in-memory count), add+remove (DB deactivate + in-memory removal), load 2 items from temp DB, get_watch_item by ID and missing
- Alerts: apr_below fires (3% < 5%), apr_above fires (25% > 20%), tvl_below fires ($80k < $100k), no-trigger case, missing pool skipped, format_alert_message, check_all_alerts
- Live: build_portfolio_summary on 0x000...001 (32 BNB on testnet), has_position=False, P&L all zeros, wallet_usd = wallet_bnb × 600

### Current State
- Full portfolio and watchlist layer is working
- ready for the dispatcher (Sprint 10) to call record_entry_value/record_gas_cost after executions
- Alerts are ready for the scheduler to call check_all_alerts each cycle

### Next Sprint
- Sprint 10: complete — see entry below.

### Notes
- `estimate_position_value` uses conservative pricing: stablecoins at $1, WBNB/BNB at live price, all other tokens at $0. This prevents overstatement of P&L but will undercount value when exotic tokens are involved.
- Token symbols are compared lowercase. Position dicts must include `token0_symbol` and `token1_symbol` as strings — executor.py should populate these when setting session.current_position.
- `check_all_alerts` accepts `prices` dict for future token-price alert checking; currently only pool-based alerts are implemented.
- Watchlist tests use `tempfile.mkstemp()` for isolated DB files, then `os.unlink()` for cleanup. The default DB (tglp_bot.db) is never touched by tests.
- The precompile address `0x000...001` has ~32 BNB on BSC Testnet (confirmed in live test). Do not rely on it for "zero balance" scenarios.

---

## Sprint 10 — Scheduler & Dispatcher — 2026-04-15

### Completed
- Implemented `core/scheduler.py`:
  - `BotScheduler` class wrapping APScheduler `BackgroundScheduler`
  - `start()` / `shutdown()` — lifecycle; `is_running` property
  - `add_user_job(chat_id, callback)` — registers interval job every CYCLE_INTERVAL_SECONDS; `replace_existing=True` prevents duplicates
  - `remove_user_job(chat_id)` — returns True if job existed, False otherwise
  - `pause_user_job(chat_id)` / `resume_user_job(chat_id)` — pause/resume without removing; return True/False
  - `has_job(chat_id)` — True for running or paused jobs
  - `active_job_count()` — total registered jobs
  - `bot_scheduler` module-level singleton
- Implemented `core/dispatcher.py`:
  - `_build_position_dict(pool_data, token_id, amount0, amount1)` — builds the `session.current_position` dict; parses token symbols from pool_data.symbol (split on '-')
  - `_handle_allocate(session, decision, snapshot, w3)` — reads wallet balance, computes amount_bnb, runs pre-execution safety checks, calls execute_allocate, updates session state and DB on success
  - `_handle_rebalance(session, decision, snapshot, w3)` — same pattern for rebalance; increments session.rebalance_count
  - `_handle_compound(session, decision, snapshot, w3)` — calls execute_compound; records gas cost
  - `run_cycle(session, notify_func, w3)` — full 8-step pipeline: operational check → snapshot → analyse → alerts → anomaly escalation → decide → execute/propose → timing warning
  - `build_cycle_callback(session, notify_func, w3)` — returns a zero-argument closure that looks up the current session from session_manager at call time (avoids stale session captures)
- Created `tests/test_sprint10.py`: 12 tests across two tiers

### Files Created/Modified
- `core/scheduler.py` — APScheduler wrapper + bot_scheduler singleton
- `core/dispatcher.py` — full cycle pipeline
- `tests/test_sprint10.py` — Sprint 10 test suite

### Tested
- Scheduler: start/stop, add job (has_job=True, count=1), remove (True/False), replace (count stays 1), pause/resume (True), pause nonexistent (False)
- Dispatcher unit: _build_position_dict keys, paused session returns immediately (no notify), locked session returns immediately (no notify), build_cycle_callback returns callable and finds session at call time
- Integration (live): full cycle on AGGRESSIVE_ALPHA session → 37 pools → 1 ALLOCATE proposal sent; anomaly counter = 0 after clean cycle

### Current State
- Scheduler and dispatcher are fully implemented
- run_cycle is wired to the full pipeline: market_data → analyser → alerts → safety → decision → execution
- notify_func decouples the dispatcher from Telegram — app.py injects it during job registration
- Execution path (auto_execute=True) is implemented but not tested end-to-end (requires funded wallet — by design)

### Notes
- `notify_func` signature: `(chat_id: int, message: str) -> None`. In app.py, this wraps `asyncio.run_coroutine_threadsafe(bot.send_message(chat_id, message), loop)` to bridge the APScheduler background thread to the asyncio Telegram event loop.
- `build_cycle_callback` looks up `session_manager.get(chat_id)` at call time, not at registration time. This ensures session mutations (position state, flags) are visible in subsequent cycles without re-registering the job.
- `misfire_grace_time = CYCLE_INTERVAL_SECONDS` — if a cycle fires late (e.g., due to a long-running API call), APScheduler will still run it if the delay is within one interval. After that, it skips and waits for the next slot.

---

## Sprint 11 — Wire Up All Commands — 2026-04-15

### Completed
- Implemented `bot/app.py` lifecycle hooks:
  - `_post_init(application)`: creates Web3 connection, builds `notify_func` closure with `asyncio.run_coroutine_threadsafe`, starts `bot_scheduler`, stores `w3` and `notify_func` in `application.bot_data`
  - `_post_shutdown(application)`: calls `bot_scheduler.shutdown()`
  - `Application.builder()` now uses `.post_init(_post_init).post_shutdown(_post_shutdown)` chain
- Updated `bot/onboarding.py` at final confirmation:
  - Replaced `# TODO Sprint 10` with real scheduler job registration using `context.application.bot_data["w3"]` and `["notify_func"]`
  - Updated confirmation message: removed "scheduler starts after /allocate" note — scheduler starts immediately on onboarding completion
- Implemented all commands in `bot/commands.py`:
  - `/dashboard`: `build_portfolio_summary()` via `run_in_executor` → position value, P&L, gas spent, system health (RPC latency, gas price)
  - `/allocate`: guards on `is_operational()`, runs `run_cycle()` in thread pool, sends "Cycle complete" on success
  - `/explore`: fetches snapshot via `run_in_executor`, calls `filter_pools_by_strategy(None)` + `score_pools()`, sends first page with `pool_list_keyboard`; stores snapshot in `context.user_data["explore_snapshot"]` for pool detail callbacks
  - `/alerts`: calls `load_watchlist(session)`, renders with `watchlist_keyboard`
  - `/history`: calls `count_trades_for_user` + `get_trades_for_user` (page=0), renders with `history_keyboard`
  - `/export`: calls `get_all_trades_for_user`, batches into ≤3800-char chunks, sends as multiple messages if needed
  - Private helper `_send_history_page(update, session, page)` reused by history callbacks
  - Private helper `_send_explore_page(update, context, scored, session, page)` reused by explore
- Wired up `bot/callbacks.py`:
  - `_handle_reset`: calls `bot_scheduler.remove_user_job(chat_id)` before `session_manager.delete()`
  - `cfg_toggle_pause`: calls `bot_scheduler.pause_user_job()` or `resume_user_job()` in sync with `session.paused`
  - `_handle_history`: real pagination using `get_trades_for_user` with `offset=page*5`
  - `_handle_pool pool_detail_*`: looks up pool from `context.user_data["explore_snapshot"]` by `pool[:20]` prefix
  - `_handle_pool pool_back_list` and `pool_page_*`: re-scores from cached snapshot
  - `_handle_watchlist alert_remove_*`: calls `remove_watch_item()`, reloads watchlist, re-renders or shows empty message
  - `_handle_action_confirm action_confirm_*`: re-runs `run_cycle()` via executor (correct: live market re-check before execution)
- Completed `bot/conversations.py` `/watch` flow — 3 states fully implemented:
  - State 0 `WATCH_AWAITING_IDENTIFIER`: receive pool address or token symbol; classify as 'pool' (starts with 0x, len≥10) or 'token'; show `_threshold_type_keyboard()`
  - State 1 `WATCH_AWAITING_THRESHOLD_TYPE`: `CallbackQueryHandler(pattern="^wt_")` maps `wt_apr_above/below`, `wt_tvl_below` to threshold_type strings; asks for numeric value
  - State 2 `WATCH_AWAITING_THRESHOLD_VALUE`: parse float ≥ 0; call `add_watch_item()`; send confirmation with watch_id and condition summary; clean up user_data
- Created `tests/test_sprint11.py`: 18 unit tests

### Files Created/Modified
- `bot/app.py` — post_init / post_shutdown hooks, Application builder chain
- `bot/onboarding.py` — real scheduler registration at onboarding completion
- `bot/commands.py` — all commands fully implemented (was all stubs)
- `bot/callbacks.py` — all TODOs wired (reset, pause, history, pool detail, alert remove, action confirm)
- `bot/conversations.py` — /watch all 3 states complete
- `tests/test_sprint11.py` — Sprint 11 test suite (18 tests)

### Tested
- All 18 Sprint 11 unit tests pass
- Import checks: all bot modules import cleanly
- `_post_init` / `_post_shutdown` are coroutine functions
- `watch_conversation_handler` has exactly 3 states with keys {0, 1, 2}
- `_threshold_type_keyboard()`: 3 buttons with `wt_` callback prefixes
- `watchlist_keyboard([])`: single `alert_noop` label; non-empty: one `alert_remove_{id}` per item
- `pool_list_keyboard`: 2 pool rows, no nav row for single page
- History page calculation: correct ceil(total / 5) for all edge cases including total=0
- `format_usd / format_bnb / format_pct / format_large_usd / escape_md`: all correct
- `add_watch_item` + `remove_watch_item`: isolated DB (tempfile) — add returns positive ID, remove returns True

### Current State
- The bot is fully wired: `python main.py` starts polling, creates Web3, starts the scheduler, and registers a cycle job for each user who completes onboarding
- Every command is live: /dashboard, /allocate, /explore, /alerts, /history, /export, /settings, /reset, /watch, /help
- The /watch conversation guides users through identifier → threshold type → threshold value in 3 steps
- Settings pause toggle is wired to `bot_scheduler.pause_user_job()` / `resume_user_job()`
- Reset is wired to `bot_scheduler.remove_user_job()`

### Notes
- `/allocate` and `action_confirm_*` use `asyncio.get_running_loop().run_in_executor(None, run_cycle, ...)` to call the synchronous dispatcher from the asyncio bot thread without blocking the event loop.
- `context.user_data["explore_snapshot"]` stores the MarketSnapshot from the last `/explore` call. Pool detail callbacks read from this cache rather than making a live RPC call.
- The `filter_pools_by_strategy(pools, strategy, analysis_result=None)` call in `/explore` passes `None` for analysis_result (no previous snapshot available at browse time). This is correct: the anomaly-exclusion filter only applies when there is a delta to compare against.
- The `watch_conversation_handler` uses `per_message=False` — the PTBUserWarning on startup is expected and informational, not an error.
- `_send_history_page` and `_send_explore_page` are private helpers in `commands.py` used by both the command handlers and the callback pagination handlers to avoid code duplication.
- Live test: 37 pools fetched, AGGRESSIVE_ALPHA strategy, ALLOCATE decision, 1 notify call with formatted decision summary.

---

## Sprint 12 — Integration Testing & Polish — 2026-04-15

### Completed
- Wrote five module-named unit test files for NEA assessment traceability:
  - `tests/test_validators.py`: 37 tests covering all 8 functions in `helpers/validators.py`
  - `tests/test_analyser.py`: 16 tests covering `core/analyser.py` — corrected to match actual `AnalysisResult` field names (`pool_deltas`, `anomalous_addresses` as set, `pools_new`/`pools_dropped` as ints, `first_run`)
  - `tests/test_decision_engine.py`: 16 tests covering `core/decision_engine.py` — corrected to use actual `ScoredPool` fields (`norm_apr`, `norm_tvl`, `score`) and `DecisionResult` constructor
  - `tests/test_portfolio.py`: 17 tests covering `core/portfolio.py`
  - `tests/test_safety.py`: 20 tests covering `core/safety.py` (19 unit + 1 live BSC Testnet gas-price check)
- Wrote `tests/test_sprint12.py`: 18 end-to-end integration tests (17 unit + 1 live BSC Testnet pipeline)
  - Session creation, snapshot filter/score, first/second cycle analysis, decision engine, run_cycle (paused/locked/proposal/snapshot/empty), portfolio summary, P&L, history (empty/insert/paginate), build_cycle_callback, _build_position_dict, live full pipeline
- Polished bot message formatting:
  - Fixed hardcoded `"safety\\-locked"` in `bot/commands.py` and `bot/callbacks.py` — now uses `escape_md("safety-locked")` for consistency
  - All error paths already had `❌`, `⚠️`, `✅`, `⏳` status indicators from Sprint 11
- Completed `README.md`: full project overview, setup instructions, command reference, strategy table, architecture diagram, database schema, known limitations
- Final `CLAUDE.md` update (this entry)

### Key Discovery: AnalysisResult interface
The test files written in Sprint 12 required careful comparison against the actual `core/analyser.py` implementation. The CLAUDE.md Sprint 5 notes described a slightly different interface than what was built. Actual fields:
- `pool_deltas: List[PoolDelta]` (not a dict named `deltas`)
- `anomalous_addresses: set` (not a list)
- `pools_new: int`, `pools_dropped: int` (not lists)
- `first_run: bool` (indicates no previous snapshot)
- `detect_anomalies(delta)` returns `List[str]` (not a mutated PoolDelta)
- `get_pool_stability_score(address, delta_history)` takes `List[PoolDelta]` (not list of dicts)

### Files Created/Modified
- `tests/test_validators.py` — NEW: 37 unit tests
- `tests/test_analyser.py` — NEW (corrected): 16 unit tests
- `tests/test_decision_engine.py` — NEW (corrected): 16 unit tests
- `tests/test_portfolio.py` — NEW: 17 unit tests
- `tests/test_safety.py` — NEW: 20 tests (19 unit + 1 live)
- `tests/test_sprint12.py` — NEW: 18 tests (17 unit + 1 live)
- `bot/commands.py` — escape_md fix for "safety-locked"
- `bot/callbacks.py` — escape_md fix for "safety-locked"
- `README.md` — complete project documentation

### Tested
- All 37 validator tests pass
- All 16 analyser tests pass
- All 16 decision engine tests pass
- All 17 portfolio tests pass
- All 19 safety unit tests pass (live Tier 2 requires BSC Testnet)
- All 17 Sprint 12 unit tests pass (live Tier 2 requires BSC Testnet)

### Current State
- **Project is complete.** All 12 sprints have been implemented and tested.
- The bot is fully functional: `python main.py` starts polling, creates Web3, starts the scheduler, and registers cycle jobs for onboarded users.
- All test files run without network access for Tier 1 tests; Tier 2 live tests require BSC Testnet connectivity.

### Notes
- The `detect_anomalies(delta)` function returns a `List[str]` (anomaly descriptions), not a modified `PoolDelta`. The caller in `analyse_cycle` checks if the list is non-empty, then sets `delta.is_anomalous = True` and populates `delta.anomaly_descriptions`.
- All database test functions use explicit `db_path=path` parameters (not monkeypatching) because Python default argument values are evaluated at definition time, making module-level constant monkeypatching unreliable for functions with `db_path: str = DB_FILENAME` defaults.
- Sprint 12 is the final sprint. The bot is feature-complete for the NEA.

---

## Sprint 13 — Feature Completeness — 2026-04-15

### Motivation
A post-Sprint-12 spec review identified three features from the original build prompt that were missing or stubbed:
1. Custom Strategy conversation handler (`custom_strategy_handler = None` placeholder)
2. History filters (pagination existed but no action-type filter UI)
3. BNB price-change alerts (only APR/TVL thresholds supported; no `price_change_pct`)

### Completed

#### Feature 1: Custom Strategy Conversation Handler
- Replaced `custom_strategy_handler = None` stub in `bot/conversations.py` with a fully implemented 7-state `ConversationHandler`
- States: CUST_PAIRS → CUST_MIN_TVL → CUST_SLIPPAGE → CUST_REBAL → CUST_COMPOUND → CUST_AUTOEXEC → CUST_CONFIRM
- Entry points: `CommandHandler("customstrategy", ...)` + `CallbackQueryHandler(pattern="^cfg_strat_custom$", ...)`
- Uses existing `custom_pairs_keyboard()` and `custom_compound_interval_keyboard()` from keyboards.py
- On confirmation: builds a `StrategyConfig` from collected values and assigns to `session.active_strategy`, `session.auto_execute`, `session.compound_enabled`
- Registered in `bot/app.py` before the catch-all CallbackQueryHandler (position 3, after onboarding and /watch)
- Added `strategy_picker_keyboard()` to `bot/keyboards.py` (shows 4 strategies + Cancel, `cfg_strat_*` prefix)
- Wired `cfg_change_strategy` in `bot/callbacks.py` to show `strategy_picker_keyboard()`
- `cfg_strat_conservative/balanced/aggressive` handled in callbacks: updates session.active_strategy from `STRATEGY_PROFILES`, then refreshes settings panel
- `cfg_strat_custom` handled by the conversation handler (entry point intercepts before catch-all); fallback message in callbacks for when conversation is not active

#### Feature 2: History Filters
- Updated `count_trades_for_user(user_chat_id, action_filter=None)` in `helpers/database.py` to accept an optional action filter
- Added `history_filter_keyboard(page, total_pages, current_filter)` to `bot/keyboards.py` — three rows of filter buttons (All, Swap, Add LP, Remove LP, Collect, Compound) above the pagination row; active filter shown with bullet (•)
- Updated `_send_history_page` in `bot/commands.py` to accept `context` parameter and `action_filter` parameter; reads `context.user_data["history_filter"]` if present
- Updated `history_command` to clear stale filter on fresh `/history` invocation
- Updated `_handle_history` in `bot/callbacks.py` to handle both `hist_filter_*` (set filter, reset to page 0) and `hist_page_*` (paginate with current filter) callbacks; stores active filter in `context.user_data["history_filter"]`

#### Feature 3: BNB Price-Change Alerts
- Added `previous_bnb_price: Optional[float] = None` field to `UserSession` in `core/strategy_manager.py`
- Added `BNB price change ≥X%` button (callback `wt_price_change`) to `_threshold_type_keyboard()` in `bot/conversations.py`
- Added `wt_price_change` → `price_change_pct` mapping in `watch_receive_threshold_type`
- Updated confirmation message labels to include `price_change_pct` condition
- Implemented `check_price_alerts(session, prices)` in `core/alerts.py`:
  - Iterates `token` watchlist items with `threshold_type == "price_change_pct"` and `identifier == "BNB"`
  - Compares `prices["BNB"]` against `session.previous_bnb_price`; fires Alert if abs % change ≥ threshold
  - Updates `session.previous_bnb_price` at the end of every call (no alert on first cycle — no baseline yet)
- Updated `check_all_alerts` in `core/alerts.py` to call `check_pool_alerts` + `check_price_alerts` and return combined list

### Files Created/Modified
- `helpers/database.py` — `count_trades_for_user` now accepts `action_filter`
- `core/strategy_manager.py` — `UserSession.previous_bnb_price` field added
- `core/alerts.py` — `check_price_alerts()` added; `check_all_alerts` calls both pool and price checkers
- `bot/keyboards.py` — `strategy_picker_keyboard()` and `history_filter_keyboard()` added
- `bot/conversations.py` — `_threshold_type_keyboard()` extended; `watch_receive_threshold_type` and `watch_receive_threshold_value` updated for `price_change_pct`; full `custom_strategy_handler` ConversationHandler implemented (7 states)
- `bot/commands.py` — `_send_history_page` accepts filter; `history_command` clears stale filter
- `bot/callbacks.py` — `_handle_settings` wired for strategy picker; `_handle_history` handles `hist_filter_*` and `hist_page_*`; `cs_` prefix fallback added to router
- `bot/app.py` — `custom_strategy_handler` imported and registered at position 3

### Tested
- Imports: all new modules import cleanly (`py -c "from bot.conversations import custom_strategy_handler; ..."`)
- PTBUserWarning for `custom_strategy_handler` (per_message=False) is expected/informational, consistent with other ConversationHandlers

### Current State
- **All three spec gaps are closed.** The bot now fully implements every feature described in the original build prompt:
  - Custom strategy setup flow: /customstrategy or Settings → Change Strategy → Custom
  - History pagination with action-type filter (All/Swap/Add LP/Remove LP/Collect/Compound)
  - BNB price-change watchlist alerts alongside existing APR/TVL alerts
- Sprint 13 is the final sprint.

### Notes
- `custom_strategy_handler` must be registered BEFORE the catch-all `CallbackQueryHandler` in `bot/app.py` so that `cfg_strat_custom` and `cs_confirm/cs_cancel` are captured when the conversation is active.
- `previous_bnb_price` is `None` on first cycle. `check_price_alerts` stores the baseline without firing, so the first alert requires at least two cycles.
- The `history_filter_keyboard` replaces `history_keyboard` in all display paths — existing code in `bot/commands.py` and `bot/callbacks.py` uses `history_filter_keyboard` directly.
- `count_trades_for_user` default `action_filter=None` is backwards-compatible; all existing callers that don't pass the argument continue to work unchanged.

---

## Sprint 14 — History Date Filters & CSV Export — 2026-04-15

### Motivation
Final spec check (post-Sprint 13) identified two remaining minor gaps:
1. `/history` filter options: action-type filter was implemented in Sprint 13, but "by date range" was also listed in the original spec. Date range buttons (Last 7 days / Last 30 days / All time) were missing.
2. `/export` format: the spec says "formatted text message or CSV-style output". Only plain text was implemented. A proper downloadable CSV file was missing.

### Completed

#### Feature 1: History Date Range Filter
- Added `since_days: Optional[int] = None` parameter to both `get_trades_for_user` and `count_trades_for_user` in `helpers/database.py`. SQL conditions are now built dynamically to handle all combinations of action-type and date-range filters cleanly.
- Added `_date_cutoff(since_days)` helper in `helpers/database.py` — computes the ISO-format UTC timestamp for N days ago. SQLite ISO string comparison is lexicographically equivalent to chronological order, so no SQL date functions are needed.
- Updated `history_filter_keyboard(page, total_pages, current_filter, current_date)` in `bot/keyboards.py` to accept a `current_date` parameter and display a fourth row: "📅 All time | 📅 Last 7d | 📅 Last 30d" with callback data `hist_date_all`, `hist_date_7`, `hist_date_30`. Active date marked with (•).
- Updated `_send_history_page` in `bot/commands.py` to read `context.user_data["history_date"]`, convert to `since_days`, and pass to both `count_trades_for_user` and `get_trades_for_user`.
- Updated `history_command` to also clear `history_date` from user_data on fresh `/history` invocation.
- Updated `_handle_history` in `bot/callbacks.py` to handle `hist_date_*` callbacks (set date filter, reset to page 0) alongside existing `hist_filter_*` (action type) and `hist_page_*` (pagination) callbacks. Both filter dimensions are preserved across page turns.

#### Feature 2: CSV Export
- Updated `export_command` in `bot/commands.py`: instead of generating text immediately, it now shows the trade count and an `export_format_keyboard()` with "📄 Text format" and "📊 CSV file" buttons.
- Added `export_format_keyboard()` to `bot/keyboards.py` — two-button keyboard with `export_fmt_text` and `export_fmt_csv` callback data.
- Extracted text generation logic from `export_command` into `_send_export_text(update_or_query, session)` — a shared helper that chunks the formatted text into ≤3800-char messages.
- Added `_send_export_csv(update_or_query, session)` — uses Python's `csv.DictWriter` and `io.BytesIO` to build an in-memory CSV and sends it as a proper downloadable `.csv` attachment via `reply_document()`. Columns: id, timestamp, action_type, pool_address, token_in, token_out, amount_in, amount_out, tx_hash, status, gas_used, gas_cost_bnb.
- Added `_handle_export` to `bot/callbacks.py` and wired the `export_*` prefix into the `handle_callback` dispatch router.

### Files Created/Modified
- `helpers/database.py` — `_date_cutoff()` helper; `since_days` param on `get_trades_for_user` and `count_trades_for_user`
- `bot/keyboards.py` — `history_filter_keyboard` gains `current_date` param and date-row; new `export_format_keyboard()`
- `bot/commands.py` — `_send_history_page` and `history_command` updated for date filter; `export_command` replaced with format-picker; `_send_export_text` and `_send_export_csv` added
- `bot/callbacks.py` — `_handle_history` handles `hist_date_*`; `export_*` added to router; `_handle_export` added

### Tested
- All imports compile cleanly: `py -c "from bot.commands import _send_export_csv; ..."` → OK

### Current State
- **All original spec requirements are now met.** Both minor gaps identified in the Sprint 13 final check are closed:
  - `/history` supports two independent filter dimensions: action type and date range
  - `/export` delivers both a human-readable text export and a downloadable CSV file

### Notes
- `_date_cutoff` uses `datetime.utcnow()` consistent with how `insert_log` / `insert_trade` write timestamps (also UTC). No timezone conversion is needed.
- Both `_send_export_text` and `_send_export_csv` accept `query.message` (a `Message` object) so they work correctly when called from a callback context.
- `reply_document()` sends the CSV as a Telegram file attachment — the user receives a downloadable `.csv` they can open in Excel or any spreadsheet app.
- Sprint 14 is the final sprint. The project is fully spec-complete.
