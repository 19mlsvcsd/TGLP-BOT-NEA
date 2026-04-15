"""
helpers/validators.py
=====================
Input validation functions for TGLP Bot.

All user-supplied data passes through this module before being used anywhere
in the system. Centralising validation here means business logic modules can
trust their inputs and focus on their own responsibilities.

Validation functions return a tuple of (is_valid: bool, error_message: str).
The error_message is empty when is_valid is True, and human-readable when False
ready to be sent directly to the user in a Telegram message.
"""

import re
from typing import Tuple

from eth_utils import is_checksum_address, to_checksum_address


def validate_private_key(key: str) -> Tuple[bool, str]:
    """
    Validate a raw private key string entered by the user.

    A valid private key is either:
    - 64 hexadecimal characters (no prefix), or
    - 66 characters starting with '0x' followed by 64 hex chars.

    Private keys are never logged or stored; this function only validates
    the format before the key is used to derive a wallet address.

    Args:
        key: The raw string provided by the user.

    Returns:
        (True, "") if the key format is valid.
        (False, human_readable_error) if the key is invalid.
    """
    if not isinstance(key, str):
        return False, "Private key must be a text string."

    stripped = key.strip()

    # Remove 0x prefix for uniform length checking.
    if stripped.startswith("0x") or stripped.startswith("0X"):
        hex_part = stripped[2:]
    else:
        hex_part = stripped

    # Must be exactly 64 hexadecimal characters.
    if len(hex_part) != 64:
        return (
            False,
            f"Private key must be 64 hex characters (got {len(hex_part)}). "
            "Check you copied it correctly.",
        )

    if not re.fullmatch(r"[0-9a-fA-F]{64}", hex_part):
        return (
            False,
            "Private key contains invalid characters. "
            "It should only contain 0-9 and a-f.",
        )

    return True, ""


def validate_ethereum_address(address: str) -> Tuple[bool, str]:
    """
    Validate an Ethereum/BSC wallet or contract address.

    Accepts both checksummed (EIP-55) and lowercased addresses, but always
    normalises to checksum format internally. A 42-character '0x'-prefixed hex
    string is required.

    Args:
        address: The address string to validate.

    Returns:
        (True, "") if valid.
        (False, human_readable_error) if invalid.
    """
    if not isinstance(address, str):
        return False, "Address must be a text string."

    stripped = address.strip()

    if not stripped.startswith("0x"):
        return False, "Address must start with '0x'."

    if len(stripped) != 42:
        return (
            False,
            f"Address must be 42 characters long (got {len(stripped)}). "
            "Check you copied it correctly.",
        )

    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", stripped):
        return False, "Address contains invalid characters."

    # Warn if the address fails EIP-55 checksum but still accept it;
    # many wallets export lowercase addresses.
    try:
        checksummed = to_checksum_address(stripped)
        if checksummed != stripped and stripped != stripped.lower():
            # Mixed-case but not valid checksum, likely a typo.
            return (
                False,
                "Address has incorrect capitalisation (failed EIP-55 checksum). "
                "Use the address exactly as your wallet exports it.",
            )
    except Exception:
        return False, "Could not parse address. Make sure it is a valid BSC address."

    return True, ""


def validate_positive_amount(value: str) -> Tuple[bool, str]:
    """
    Validate that a user-supplied string represents a positive numeric amount.

    Accepts integer and decimal strings (e.g., "10", "0.5", "1.23456").
    Rejects zero, negative values, and non-numeric input.

    Args:
        value: The raw string amount provided by the user.

    Returns:
        (True, "") if the amount is valid and positive.
        (False, human_readable_error) otherwise.
    """
    if not isinstance(value, str):
        return False, "Amount must be a text string."

    stripped = value.strip()

    try:
        numeric = float(stripped)
    except ValueError:
        return False, f"'{stripped}' is not a valid number. Enter a positive amount like 0.5 or 100."

    if numeric <= 0:
        return False, "Amount must be greater than zero."

    # Guard against astronomically large values that are almost certainly typos.
    if numeric > 1_000_000_000:
        return False, "Amount seems too large. Please double-check the value."

    return True, ""


def validate_slippage(value: str) -> Tuple[bool, str]:
    """
    Validate a slippage tolerance percentage string.

    Valid range: 0.1% to 5.0% (inclusive). The user enters a percentage
    (e.g., "0.5" means 0.5%) and this function validates it is within the
    safe range. Too-low slippage causes unnecessary transaction failures;
    too-high slippage exposes the user to front-running (sandwich attacks).

    Args:
        value: The percentage string, e.g., "0.5" for 0.5% slippage.

    Returns:
        (True, "") if valid.
        (False, human_readable_error) otherwise.
    """
    if not isinstance(value, str):
        return False, "Slippage must be a text string."

    stripped = value.strip()

    try:
        percentage = float(stripped)
    except ValueError:
        return (
            False,
            f"'{stripped}' is not a valid number. Enter a slippage percentage like 0.5.",
        )

    if percentage < 0.1:
        return (
            False,
            f"Slippage of {percentage}% is too low. Minimum is 0.1%. "
            "Very low slippage causes transactions to fail frequently.",
        )

    if percentage > 5.0:
        return (
            False,
            f"Slippage of {percentage}% is too high. Maximum is 5.0%. "
            "High slippage makes you vulnerable to front-running bots.",
        )

    return True, ""


def validate_tvl_threshold(value: str) -> Tuple[bool, str]:
    """
    Validate a minimum TVL (Total Value Locked) threshold in USD.

    The value must be a positive number. TVL thresholds below $1,000 are
    rejected as they indicate pools with almost no liquidity, which poses
    extreme impermanent loss and exit-liquidity risk.

    Args:
        value: The USD TVL threshold string, e.g., "500000".

    Returns:
        (True, "") if valid.
        (False, human_readable_error) otherwise.
    """
    valid, error = validate_positive_amount(value)
    if not valid:
        return False, error

    tvl = float(value.strip())

    if tvl < 1_000:
        return (
            False,
            f"Minimum TVL of ${tvl:,.0f} is too low. "
            "Set at least $1,000 to avoid pools with insufficient liquidity.",
        )

    return True, ""


def validate_apr_threshold(value: str) -> Tuple[bool, str]:
    """
    Validate an APR alert threshold percentage.

    Accepts values between 0% and 10,000%. Negative values and absurdly high
    values (above 10,000%) are rejected as almost certainly erroneous input.

    Args:
        value: The APR percentage string, e.g., "5.0" for 5%.

    Returns:
        (True, "") if valid.
        (False, human_readable_error) otherwise.
    """
    if not isinstance(value, str):
        return False, "APR threshold must be a text string."

    stripped = value.strip()

    try:
        apr = float(stripped)
    except ValueError:
        return False, f"'{stripped}' is not a valid number. Enter an APR percentage like 5.0."

    if apr < 0:
        return False, "APR threshold cannot be negative."

    if apr > 10_000:
        return (
            False,
            f"APR of {apr}% seems unrealistically high. "
            "If you really mean this, contact the developer.",
        )

    return True, ""


def normalise_private_key(key: str) -> str:
    """
    Normalise a private key to a consistent format without '0x' prefix.

    Call this only after validate_private_key() has returned True.

    Args:
        key: A validated private key string (with or without '0x').

    Returns:
        The key as a plain 64-character lowercase hex string.
    """
    stripped = key.strip()
    if stripped.startswith("0x") or stripped.startswith("0X"):
        return stripped[2:].lower()
    return stripped.lower()


def normalise_address(address: str) -> str:
    """
    Normalise an Ethereum address to EIP-55 checksum format.

    Call this only after validate_ethereum_address() has returned True.

    Args:
        address: A validated address string.

    Returns:
        The address in checksummed form, e.g., '0xAb5801a...'.
    """
    return to_checksum_address(address.strip())
