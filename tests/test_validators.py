"""
tests/test_validators.py
========================
Unit tests for helpers/validators.py.

Covers every public function with valid inputs, boundary values, and
rejection cases. No network access required.

Run with:
  python tests/test_validators.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helpers.validators import (
    normalise_address,
    normalise_private_key,
    validate_apr_threshold,
    validate_ethereum_address,
    validate_positive_amount,
    validate_private_key,
    validate_slippage,
    validate_tvl_threshold,
)


# ---------------------------------------------------------------------------
# validate_private_key
# ---------------------------------------------------------------------------

def test_private_key_valid_64_hex():
    ok, msg = validate_private_key("a" * 64)
    assert ok is True and msg == ""
    print("[PASS] validate_private_key — 64-char hex accepted")


def test_private_key_valid_0x_prefix():
    ok, msg = validate_private_key("0x" + "b" * 64)
    assert ok is True and msg == ""
    print("[PASS] validate_private_key — 0x-prefixed 66-char accepted")


def test_private_key_valid_uppercase_hex():
    ok, msg = validate_private_key("A" * 64)
    assert ok is True and msg == ""
    print("[PASS] validate_private_key — uppercase hex accepted")


def test_private_key_too_short():
    ok, msg = validate_private_key("a" * 32)
    assert ok is False
    assert "64 hex" in msg
    print("[PASS] validate_private_key — 32-char key rejected")


def test_private_key_too_long():
    ok, msg = validate_private_key("a" * 65)
    assert ok is False
    print("[PASS] validate_private_key — 65-char key rejected")


def test_private_key_invalid_chars():
    ok, msg = validate_private_key("g" * 64)  # 'g' is not hex
    assert ok is False
    assert "invalid characters" in msg
    print("[PASS] validate_private_key — non-hex chars rejected")


def test_private_key_empty():
    ok, msg = validate_private_key("")
    assert ok is False
    print("[PASS] validate_private_key — empty string rejected")


def test_private_key_not_string():
    ok, msg = validate_private_key(12345)
    assert ok is False
    print("[PASS] validate_private_key — non-string rejected")


# ---------------------------------------------------------------------------
# validate_ethereum_address
# ---------------------------------------------------------------------------

def test_address_valid_checksummed():
    # A well-known BSC Testnet address (PancakeSwap factory, checksummed).
    addr = "0x6725F303b657a9451d8BA641348b6761A6CC7a17"
    ok, msg = validate_ethereum_address(addr)
    assert ok is True and msg == ""
    print("[PASS] validate_ethereum_address — valid checksummed address accepted")


def test_address_valid_lowercase():
    # Lowercase form of a valid address.
    addr = "0x" + "a" * 40
    ok, msg = validate_ethereum_address(addr)
    assert ok is True and msg == ""
    print("[PASS] validate_ethereum_address — valid lowercase address accepted")


def test_address_missing_0x():
    ok, msg = validate_ethereum_address("ab" * 20)
    assert ok is False
    assert "0x" in msg
    print("[PASS] validate_ethereum_address — missing 0x rejected")


def test_address_too_short():
    ok, msg = validate_ethereum_address("0x" + "a" * 20)
    assert ok is False
    assert "42 characters" in msg
    print("[PASS] validate_ethereum_address — 22-char address rejected")


def test_address_invalid_chars():
    ok, msg = validate_ethereum_address("0x" + "g" * 40)
    assert ok is False
    print("[PASS] validate_ethereum_address — non-hex chars rejected")


def test_address_not_string():
    ok, msg = validate_ethereum_address(None)
    assert ok is False
    print("[PASS] validate_ethereum_address — non-string rejected")


# ---------------------------------------------------------------------------
# validate_positive_amount
# ---------------------------------------------------------------------------

def test_positive_amount_integer():
    ok, msg = validate_positive_amount("100")
    assert ok is True and msg == ""
    print("[PASS] validate_positive_amount — integer '100' accepted")


def test_positive_amount_decimal():
    ok, msg = validate_positive_amount("0.5")
    assert ok is True and msg == ""
    print("[PASS] validate_positive_amount — decimal '0.5' accepted")


def test_positive_amount_zero():
    ok, msg = validate_positive_amount("0")
    assert ok is False
    assert "greater than zero" in msg
    print("[PASS] validate_positive_amount — zero rejected")


def test_positive_amount_negative():
    ok, msg = validate_positive_amount("-1")
    assert ok is False
    print("[PASS] validate_positive_amount — negative rejected")


def test_positive_amount_non_numeric():
    ok, msg = validate_positive_amount("abc")
    assert ok is False
    assert "not a valid number" in msg
    print("[PASS] validate_positive_amount — non-numeric string rejected")


def test_positive_amount_whitespace():
    # Leading/trailing whitespace is accepted (stripped internally).
    ok, msg = validate_positive_amount("  1.5  ")
    assert ok is True and msg == ""
    print("[PASS] validate_positive_amount — whitespace-padded value accepted")


# ---------------------------------------------------------------------------
# validate_slippage
# ---------------------------------------------------------------------------

def test_slippage_valid_midrange():
    ok, msg = validate_slippage("0.5")
    assert ok is True and msg == ""
    print("[PASS] validate_slippage — 0.5% accepted")


def test_slippage_valid_minimum():
    ok, msg = validate_slippage("0.1")
    assert ok is True and msg == ""
    print("[PASS] validate_slippage — 0.1% boundary accepted")


def test_slippage_valid_maximum():
    ok, msg = validate_slippage("5.0")
    assert ok is True and msg == ""
    print("[PASS] validate_slippage — 5.0% boundary accepted")


def test_slippage_too_low():
    ok, msg = validate_slippage("0.05")
    assert ok is False
    assert "too low" in msg
    print("[PASS] validate_slippage — 0.05% rejected as too low")


def test_slippage_too_high():
    ok, msg = validate_slippage("6.0")
    assert ok is False
    assert "too high" in msg
    print("[PASS] validate_slippage — 6.0% rejected as too high")


def test_slippage_non_numeric():
    ok, msg = validate_slippage("half")
    assert ok is False
    print("[PASS] validate_slippage — non-numeric string rejected")


# ---------------------------------------------------------------------------
# validate_tvl_threshold
# ---------------------------------------------------------------------------

def test_tvl_valid():
    ok, msg = validate_tvl_threshold("100000")
    assert ok is True and msg == ""
    print("[PASS] validate_tvl_threshold — $100,000 accepted")


def test_tvl_minimum_boundary():
    ok, msg = validate_tvl_threshold("1000")
    assert ok is True and msg == ""
    print("[PASS] validate_tvl_threshold — $1,000 boundary accepted")


def test_tvl_too_low():
    ok, msg = validate_tvl_threshold("500")
    assert ok is False
    assert "too low" in msg
    print("[PASS] validate_tvl_threshold — $500 rejected as too low")


def test_tvl_zero():
    ok, msg = validate_tvl_threshold("0")
    assert ok is False
    print("[PASS] validate_tvl_threshold — zero rejected")


# ---------------------------------------------------------------------------
# validate_apr_threshold
# ---------------------------------------------------------------------------

def test_apr_valid():
    ok, msg = validate_apr_threshold("5.0")
    assert ok is True and msg == ""
    print("[PASS] validate_apr_threshold — 5.0% accepted")


def test_apr_zero():
    # Zero APR threshold is valid (alert when APR drops to 0).
    ok, msg = validate_apr_threshold("0")
    assert ok is True and msg == ""
    print("[PASS] validate_apr_threshold — 0% accepted")


def test_apr_negative():
    ok, msg = validate_apr_threshold("-1")
    assert ok is False
    assert "negative" in msg
    print("[PASS] validate_apr_threshold — negative rejected")


def test_apr_absurdly_high():
    ok, msg = validate_apr_threshold("99999")
    assert ok is False
    assert "high" in msg
    print("[PASS] validate_apr_threshold — 99,999% rejected")


# ---------------------------------------------------------------------------
# normalise_private_key
# ---------------------------------------------------------------------------

def test_normalise_key_strips_prefix():
    result = normalise_private_key("0x" + "A" * 64)
    assert result == "a" * 64
    print("[PASS] normalise_private_key — strips 0x and lowercases")


def test_normalise_key_no_prefix():
    result = normalise_private_key("B" * 64)
    assert result == "b" * 64
    print("[PASS] normalise_private_key — lowercases unprefixed key")


# ---------------------------------------------------------------------------
# normalise_address
# ---------------------------------------------------------------------------

def test_normalise_address_returns_checksum():
    lowercase = "0x" + "a" * 40
    result = normalise_address(lowercase)
    assert result.startswith("0x")
    assert len(result) == 42
    print("[PASS] normalise_address — returns checksummed address")


# ===========================================================================
# Run all tests
# ===========================================================================

if __name__ == "__main__":
    # validate_private_key
    test_private_key_valid_64_hex()
    test_private_key_valid_0x_prefix()
    test_private_key_valid_uppercase_hex()
    test_private_key_too_short()
    test_private_key_too_long()
    test_private_key_invalid_chars()
    test_private_key_empty()
    test_private_key_not_string()

    # validate_ethereum_address
    test_address_valid_checksummed()
    test_address_valid_lowercase()
    test_address_missing_0x()
    test_address_too_short()
    test_address_invalid_chars()
    test_address_not_string()

    # validate_positive_amount
    test_positive_amount_integer()
    test_positive_amount_decimal()
    test_positive_amount_zero()
    test_positive_amount_negative()
    test_positive_amount_non_numeric()
    test_positive_amount_whitespace()

    # validate_slippage
    test_slippage_valid_midrange()
    test_slippage_valid_minimum()
    test_slippage_valid_maximum()
    test_slippage_too_low()
    test_slippage_too_high()
    test_slippage_non_numeric()

    # validate_tvl_threshold
    test_tvl_valid()
    test_tvl_minimum_boundary()
    test_tvl_too_low()
    test_tvl_zero()

    # validate_apr_threshold
    test_apr_valid()
    test_apr_zero()
    test_apr_negative()
    test_apr_absurdly_high()

    # normalise functions
    test_normalise_key_strips_prefix()
    test_normalise_key_no_prefix()
    test_normalise_address_returns_checksum()

    print()
    print("All validator tests passed.")
