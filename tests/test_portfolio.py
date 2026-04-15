"""
tests/test_portfolio.py
=======================
Unit tests for core/portfolio.py.

Covers position valuation, P&L calculation, and session-state accumulators.
No network access required — build_portfolio_summary is tested with a mock
w3 object so no RPC calls are made.

Run with:
  python tests/test_portfolio.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.portfolio import (
    PnLResult,
    PortfolioSummary,
    PositionValue,
    _token_usd_value,
    calculate_pnl,
    estimate_position_value,
    record_entry_value,
    record_gas_cost,
)
from core.strategy_manager import UserSession
from config.settings import BALANCED_GROWTH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(chat_id=80001, entry_usd=0.0, gas_bnb=0.0, rebalance=0):
    s = UserSession(
        chat_id=chat_id,
        wallet_address="0x" + "0" * 40,
        private_key="0x" + "a" * 64,
        active_strategy=BALANCED_GROWTH,
        compound_enabled=False,
        auto_execute=False,
    )
    s.entry_value_usd = entry_usd
    s.total_gas_spent_bnb = gas_bnb
    s.rebalance_count = rebalance
    return s


def _position(sym0="USDT", sym1="WBNB", amt0=500.0, amt1=1.0):
    return {
        "token0_symbol": sym0,
        "token1_symbol": sym1,
        "amount0": amt0,
        "amount1": amt1,
    }


# ---------------------------------------------------------------------------
# _token_usd_value
# ---------------------------------------------------------------------------

def test_stablecoin_prices_at_one():
    """All stablecoins must be priced at $1 regardless of BNB price."""
    bnb_price = 600.0
    for sym in ("usdt", "USDT", "usdc", "USDC", "busd", "BUSD", "dai", "DAI"):
        val = _token_usd_value(100.0, sym, bnb_price)
        assert val == 100.0, f"{sym}: expected $100.00, got ${val}"
    print("[PASS] _token_usd_value — all stablecoins price at $1")


def test_wbnb_priced_at_bnb_rate():
    """WBNB and BNB must multiply amount by bnb_price_usd."""
    bnb_price = 600.0
    for sym in ("WBNB", "wbnb", "BNB", "bnb"):
        val = _token_usd_value(2.0, sym, bnb_price)
        assert val == 1200.0, f"{sym}: expected $1200.00, got ${val}"
    print("[PASS] _token_usd_value — WBNB/BNB priced at BNB rate")


def test_unknown_token_prices_at_zero():
    """Unknown tokens must return $0 (conservative: no oracle available)."""
    val = _token_usd_value(1000.0, "CAKE", 600.0)
    assert val == 0.0
    val2 = _token_usd_value(50.0, "MBOX", 600.0)
    assert val2 == 0.0
    print("[PASS] _token_usd_value — unknown tokens price at $0")


# ---------------------------------------------------------------------------
# estimate_position_value
# ---------------------------------------------------------------------------

def test_position_value_stablecoin_pair():
    """USDT + USDC pair: both at $1, total = sum of amounts."""
    pos = _position(sym0="USDT", sym1="USDC", amt0=400.0, amt1=400.0)
    pv = estimate_position_value(pos, bnb_price_usd=600.0)
    assert pv.value_usd == 800.0
    assert pv.token0_symbol == "USDT"
    assert pv.token1_symbol == "USDC"
    print("[PASS] estimate_position_value — USDT+USDC = sum of amounts")


def test_position_value_stable_wbnb_pair():
    """USDT + WBNB: $500 USDT + 1 WBNB × $600 = $1100."""
    pos = _position(sym0="USDT", sym1="WBNB", amt0=500.0, amt1=1.0)
    pv = estimate_position_value(pos, bnb_price_usd=600.0)
    assert pv.value_usd == 1100.0
    print("[PASS] estimate_position_value — USDT+WBNB = $1100 at $600/BNB")


def test_position_value_unknown_tokens_zero():
    """Pair of unknown tokens must value at $0 (conservative)."""
    pos = _position(sym0="CAKE", sym1="MBOX", amt0=100.0, amt1=100.0)
    pv = estimate_position_value(pos, bnb_price_usd=600.0)
    assert pv.value_usd == 0.0
    print("[PASS] estimate_position_value — unknown tokens value at $0")


def test_position_value_bnb_conversion():
    """value_bnb must equal value_usd / bnb_price_usd."""
    pos = _position(sym0="USDT", sym1="USDT", amt0=600.0, amt1=600.0)
    pv = estimate_position_value(pos, bnb_price_usd=600.0)
    assert pv.value_usd == 1200.0
    assert abs(pv.value_bnb - 2.0) < 0.001
    print("[PASS] estimate_position_value — value_bnb = value_usd / bnb_price")


# ---------------------------------------------------------------------------
# calculate_pnl
# ---------------------------------------------------------------------------

def test_pnl_profit():
    """Current value > entry value → positive unrealised P&L."""
    session = _session(entry_usd=1000.0, gas_bnb=0.01)
    pnl = calculate_pnl(session, current_value_usd=1200.0, bnb_price_usd=600.0)
    assert pnl.unrealised_pnl_usd == 200.0
    assert pnl.unrealised_pnl_pct == 20.0
    assert pnl.gas_cost_usd == 6.0   # 0.01 BNB × $600
    assert pnl.net_pnl_usd == 194.0  # $200 - $6
    print("[PASS] calculate_pnl — profit scenario correct")


def test_pnl_loss():
    """Current value < entry value → negative unrealised P&L."""
    session = _session(entry_usd=1000.0, gas_bnb=0.0)
    pnl = calculate_pnl(session, current_value_usd=800.0, bnb_price_usd=600.0)
    assert pnl.unrealised_pnl_usd == -200.0
    assert pnl.unrealised_pnl_pct == -20.0
    print("[PASS] calculate_pnl — loss scenario correct")


def test_pnl_zero_entry_no_division_error():
    """When entry_value_usd=0, pct must be 0.0 (no division by zero)."""
    session = _session(entry_usd=0.0, gas_bnb=0.0)
    pnl = calculate_pnl(session, current_value_usd=0.0, bnb_price_usd=600.0)
    assert pnl.unrealised_pnl_pct == 0.0
    print("[PASS] calculate_pnl — zero entry does not raise division error")


def test_pnl_rebalance_count_included():
    """Rebalance count from session must appear in PnLResult."""
    session = _session(entry_usd=1000.0, rebalance=3)
    pnl = calculate_pnl(session, current_value_usd=1000.0, bnb_price_usd=600.0)
    assert pnl.rebalance_count == 3
    print("[PASS] calculate_pnl — rebalance_count passed through correctly")


# ---------------------------------------------------------------------------
# record_entry_value
# ---------------------------------------------------------------------------

def test_record_entry_value_sets_session():
    """record_entry_value must update session.entry_value_usd."""
    session = _session(entry_usd=0.0)
    record_entry_value(session, 1500.0)
    assert session.entry_value_usd == 1500.0
    print("[PASS] record_entry_value — updates session.entry_value_usd")


def test_record_entry_value_overwrites():
    """Calling record_entry_value twice must overwrite, not accumulate."""
    session = _session(entry_usd=1000.0)
    record_entry_value(session, 2000.0)
    assert session.entry_value_usd == 2000.0
    print("[PASS] record_entry_value — overwrites previous entry value")


# ---------------------------------------------------------------------------
# record_gas_cost
# ---------------------------------------------------------------------------

def test_record_gas_cost_accumulates():
    """record_gas_cost must accumulate into session.total_gas_spent_bnb."""
    session = _session(gas_bnb=0.0)
    record_gas_cost(session, 0.001)
    record_gas_cost(session, 0.002)
    assert abs(session.total_gas_spent_bnb - 0.003) < 1e-9
    print("[PASS] record_gas_cost — gas costs accumulate correctly")


def test_record_gas_cost_starts_from_existing():
    """record_gas_cost must add to any pre-existing total."""
    session = _session(gas_bnb=0.005)
    record_gas_cost(session, 0.001)
    assert abs(session.total_gas_spent_bnb - 0.006) < 1e-9
    print("[PASS] record_gas_cost — adds to pre-existing gas total")


# ---------------------------------------------------------------------------
# PortfolioSummary fields
# ---------------------------------------------------------------------------

def test_portfolio_summary_no_position_structure():
    """With no position, has_position=False and position_value=None."""
    from unittest.mock import MagicMock
    session = _session()
    session.current_position = None

    from core.portfolio import build_portfolio_summary

    # Mock w3 so get_bnb_balance returns a fixed value without RPC.
    mock_w3 = MagicMock()
    import core.portfolio as port_mod
    original_fn = port_mod.get_bnb_balance
    port_mod.get_bnb_balance = lambda w3, addr: 2.0

    try:
        summary = build_portfolio_summary(mock_w3, session, bnb_price_usd=600.0)
        assert summary.has_position is False
        assert summary.position_value is None
        assert summary.wallet_bnb == 2.0
        assert summary.wallet_usd == 1200.0
    finally:
        port_mod.get_bnb_balance = original_fn

    print("[PASS] build_portfolio_summary — no position: has_position=False, wallet_bnb correct")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # _token_usd_value
    test_stablecoin_prices_at_one()
    test_wbnb_priced_at_bnb_rate()
    test_unknown_token_prices_at_zero()

    # estimate_position_value
    test_position_value_stablecoin_pair()
    test_position_value_stable_wbnb_pair()
    test_position_value_unknown_tokens_zero()
    test_position_value_bnb_conversion()

    # calculate_pnl
    test_pnl_profit()
    test_pnl_loss()
    test_pnl_zero_entry_no_division_error()
    test_pnl_rebalance_count_included()

    # record_entry_value / record_gas_cost
    test_record_entry_value_sets_session()
    test_record_entry_value_overwrites()
    test_record_gas_cost_accumulates()
    test_record_gas_cost_starts_from_existing()

    # build_portfolio_summary
    test_portfolio_summary_no_position_structure()

    print()
    print("All portfolio tests passed.")
