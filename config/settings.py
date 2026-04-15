"""
config/settings.py
==================
Central configuration file for TGLP Bot.

All constants, contract addresses, strategy profile definitions, scoring
weights, anomaly thresholds, and token lists live here. No magic numbers
should appear anywhere else in the codebase; import from this module instead.

Role in the system: every other module imports from settings.py. Changing a
value here propagates throughout the entire bot without touching business logic.
"""

from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Network configuration
# ---------------------------------------------------------------------------

# BSC Testnet public RPC endpoint. This is overridden by the BSC_TESTNET_RPC_URL
# environment variable if set in .env.
BSC_TESTNET_RPC_URL: str = "https://data-seed-prebsc-1-s1.binance.org:8545/"

# BSC Testnet chain ID, used to validate the connected network and to sign
# transactions so they are rejected on mainnet if accidentally broadcast there.
BSC_TESTNET_CHAIN_ID: int = 97

# BSCScan Testnet explorer base URL, used to build clickable tx hash links.
BSCSCAN_TESTNET_URL: str = "https://testnet.bscscan.com/tx/"

# Maximum number of seconds to wait for a transaction to be mined before
# treating it as a timeout failure.
TX_RECEIPT_TIMEOUT: int = 120


# ---------------------------------------------------------------------------
# PancakeSwap V3 contract addresses (BSC Testnet)
# ---------------------------------------------------------------------------
# These are the official PancakeSwap V3 deployments on BSC Testnet.
# Mainnet addresses are documented here for reference only; the bot always
# connects to testnet.

PANCAKE_V3_FACTORY: str = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
PANCAKE_V3_ROUTER: str = "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4"
PANCAKE_V3_POSITION_MANAGER: str = "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364"
PANCAKE_V3_MASTER_CHEF: str = "0x556B9306565093C855AEA9AE92A594704c2Cd59e"

# ABI file paths, relative to the project root.
ABI_DIR: str = "config/abi"
ABI_ERC20: str = f"{ABI_DIR}/erc20.json"
ABI_FACTORY: str = f"{ABI_DIR}/pancake_factory_v3.json"
ABI_ROUTER: str = f"{ABI_DIR}/pancake_router_v3.json"
ABI_POSITION_MANAGER: str = f"{ABI_DIR}/pancake_position_manager.json"
ABI_POOL: str = f"{ABI_DIR}/pancake_pool_v3.json"


# ---------------------------------------------------------------------------
# Token lists
# ---------------------------------------------------------------------------
# Used by market_data.py to classify pool pairs into categories.
# Addresses are checksummed BSC Testnet equivalents where available.

# Stablecoins: tokens that maintain a ~$1 peg.
STABLECOIN_ADDRESSES: List[str] = [
    "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",  # USDT (BSC Testnet)
    "0xaB1a4d4f1D656d2450692D237fdD6C7f9146e814",  # USDC (BSC Testnet)
    "0xeD24FC36d5Ee211Ea25A80239Fb8C4Cfd80f12Ee",  # BUSD (BSC Testnet)
]

# Stablecoin symbols (lowercase) for classification via symbol strings from APIs.
STABLECOIN_SYMBOLS: List[str] = ["usdt", "usdc", "busd", "dai", "tusd", "usdp"]

# Large-cap tokens: non-stable tokens with high market capitalisation and
# sufficient liquidity that the strategy considers them lower-risk.
LARGECAP_SYMBOLS: List[str] = ["bnb", "wbnb", "eth", "weth", "btc", "wbtc", "cake"]


# ---------------------------------------------------------------------------
# Scheduler configuration
# ---------------------------------------------------------------------------

# How frequently the bot runs its analysis-decision-execution cycle, in seconds.
# Set to 15 seconds for responsive testnet behaviour; increase for mainnet.
CYCLE_INTERVAL_SECONDS: int = 15

# If a cycle takes longer than this many seconds, log a warning.
CYCLE_TIMEOUT_WARNING_SECONDS: int = 10


# ---------------------------------------------------------------------------
# Default trading parameters
# ---------------------------------------------------------------------------

# Default slippage tolerance (as a fraction, not a percentage).
# 0.005 = 0.5%, the maximum acceptable price deviation from the quoted amount.
DEFAULT_SLIPPAGE: float = 0.005

# Deadline offset in seconds added to the current timestamp when building
# swap/liquidity transactions. Transactions that are not mined within this
# window will revert on-chain.
TX_DEADLINE_OFFSET: int = 300  # 5 minutes

# Minimum BNB balance required before the bot will attempt any transaction.
# Ensures the wallet can always pay gas.
MIN_BNB_FOR_GAS: float = 0.005  # BNB


# ---------------------------------------------------------------------------
# Gas configuration
# ---------------------------------------------------------------------------

# Maximum gas price (in Gwei) the bot will accept without warning the user.
# BSC Testnet gas prices are typically very low; this acts as a sanity check.
GAS_PRICE_WARNING_GWEI: float = 10.0

# Hard ceiling on gas price; execution is blocked above this level.
# Set to twice the warning threshold as a conservative hard stop.
MAX_GAS_PRICE_GWEI: float = 20.0

# Maximum fraction of the wallet balance that may be placed into a single
# LP position. Prevents the bot from putting all funds at risk in one trade.
MAX_POSITION_FRACTION: float = 0.90  # 90%

# Number of consecutive anomalous market cycles before the safety controller
# engages a safety lock on the session. Prevents acting on sustained bad data.
SAFETY_ANOMALY_LOCK_THRESHOLD: int = 3

# Gas limits for common operations. These are conservative upper bounds;
# actual gas usage will usually be lower.
GAS_LIMIT_APPROVE: int = 60_000
GAS_LIMIT_SWAP: int = 300_000
GAS_LIMIT_ADD_LIQUIDITY: int = 500_000
GAS_LIMIT_REMOVE_LIQUIDITY: int = 400_000
GAS_LIMIT_COLLECT: int = 200_000


# ---------------------------------------------------------------------------
# Market data & API configuration
# ---------------------------------------------------------------------------

# DeFiLlama yields API endpoint, returns pool data including APR and TVL.
# Note: this returns mainnet data. For testnet development, we use this data
# for pool discovery and scoring, then execute on testnet. Documented limitation.
DEFI_LLAMA_POOLS_URL: str = "https://yields.llama.fi/pools"

# Binance public API endpoint for spot prices.
BINANCE_PRICE_URL: str = "https://api.binance.com/api/v3/ticker/price"

# Tokens to fetch prices for from Binance.
PRICE_SYMBOLS: List[str] = ["BNBUSDT", "ETHUSDT", "BTCUSDT", "CAKEUSDT"]

# How long to cache a market snapshot before fetching fresh data (seconds).
SNAPSHOT_CACHE_SECONDS: int = 30

# Number of retries for failed API calls before skipping with a warning.
API_RETRY_COUNT: int = 1


# ---------------------------------------------------------------------------
# Anomaly detection thresholds
# ---------------------------------------------------------------------------
# These values define what counts as an "anomaly" in the analyser module.
# Anomalous pools are excluded from scoring to avoid acting on bad data.

# APR increase above this fraction from one cycle to the next is flagged.
# 0.5 = 50% increase, e.g., APR jumping from 10% to 15% in one cycle.
ANOMALY_APR_SPIKE_THRESHOLD: float = 0.50

# TVL decrease below this fraction triggers an anomaly flag.
# 0.30 = 30% drop, meaning a pool losing a third of its TVL in one cycle is suspicious.
ANOMALY_TVL_DROP_THRESHOLD: float = 0.30

# Price deviation above this fraction from one cycle is flagged.
# 0.10 = 10%, meaning a 10% price move in 15 seconds is almost certainly bad data.
ANOMALY_PRICE_DEVIATION_THRESHOLD: float = 0.10


# ---------------------------------------------------------------------------
# Pool scoring weights
# ---------------------------------------------------------------------------
# Weights must sum to 1.0. Used by decision_engine.py to rank pools.

SCORE_WEIGHT_APR: float = 0.40
SCORE_WEIGHT_TVL: float = 0.30
SCORE_WEIGHT_STABILITY: float = 0.20
SCORE_WEIGHT_VOLUME: float = 0.10


# ---------------------------------------------------------------------------
# Strategy profile definitions
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    """
    Defines the parameters for a single named strategy profile.

    Attributes:
        name:                 Display name shown to the user.
        description:          Brief explanation of the strategy's goals.
        allowed_pair_types:   List of pair type strings this strategy targets.
                              Values: 'stable-stable', 'stable-largecap',
                              'largecap-largecap'.
        min_tvl_usd:          Minimum pool TVL (in USD) for a pool to qualify.
        max_slippage:         Maximum acceptable slippage as a fraction (0.005 = 0.5%).
        rebalance_threshold:  Score difference fraction that triggers a rebalance.
                              0.15 = rebalance if a new pool scores 15% better.
        compound_interval:    Seconds between automatic fee compounding.
                              None disables auto-compound.
        auto_execute:         If True, execute decisions automatically.
                              If False, always prompt the user for confirmation.
    """
    name: str
    description: str
    allowed_pair_types: List[str]
    min_tvl_usd: float
    max_slippage: float
    rebalance_threshold: float
    compound_interval: int | None
    auto_execute: bool


# Conservative Yield: minimises risk by staying in stablecoin pairs only.
# Suitable for users who prioritise capital preservation over returns.
CONSERVATIVE_YIELD: StrategyConfig = StrategyConfig(
    name="Conservative Yield",
    description=(
        "Stablecoin pairs only. Low slippage, high TVL requirement, "
        "auto-compound enabled. Prioritises capital safety over returns."
    ),
    allowed_pair_types=["stable-stable"],
    min_tvl_usd=500_000,
    max_slippage=0.003,   # 0.3%
    rebalance_threshold=0.20,
    compound_interval=3600,  # compound every hour
    auto_execute=True,
)

# Balanced Growth: mixed exposure between stablecoins and large-cap assets.
# Suitable for users who accept moderate risk for higher potential returns.
BALANCED_GROWTH: StrategyConfig = StrategyConfig(
    name="Balanced Growth",
    description=(
        "Stablecoin + large-cap pairs. Moderate slippage, medium TVL, "
        "auto-compound enabled. Balances yield with manageable risk."
    ),
    allowed_pair_types=["stable-stable", "stable-largecap"],
    min_tvl_usd=200_000,
    max_slippage=0.005,   # 0.5%
    rebalance_threshold=0.15,
    compound_interval=1800,  # compound every 30 minutes
    auto_execute=True,
)

# Aggressive Alpha: large-cap pairs, prioritises APR over stability.
# Suitable for experienced users comfortable with higher impermanent loss risk.
AGGRESSIVE_ALPHA: StrategyConfig = StrategyConfig(
    name="Aggressive Alpha",
    description=(
        "Large-cap pairs. Higher slippage tolerance, lower TVL minimum, "
        "seeks highest APR. Accepts higher impermanent loss risk."
    ),
    allowed_pair_types=["stable-largecap", "largecap-largecap"],
    min_tvl_usd=100_000,
    max_slippage=0.010,   # 1.0%
    rebalance_threshold=0.10,
    compound_interval=None,  # user decides at setup
    auto_execute=False,      # always confirm for aggressive actions
)

# All pre-defined profiles indexed by a short key for easy lookup.
STRATEGY_PROFILES: dict[str, StrategyConfig] = {
    "conservative": CONSERVATIVE_YIELD,
    "balanced": BALANCED_GROWTH,
    "aggressive": AGGRESSIVE_ALPHA,
}

# Custom is handled separately; user supplies all parameters interactively.
CUSTOM_STRATEGY_KEY: str = "custom"


# ---------------------------------------------------------------------------
# SQLite database
# ---------------------------------------------------------------------------

# Database filename. Created in the project root at runtime.
DB_FILENAME: str = "tglp_bot.db"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
