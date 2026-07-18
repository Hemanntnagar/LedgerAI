"""
Normalize Indian phone numbers for customer identity / loyalty.

Canonical form is E.164 with India country code: +91XXXXXXXXXX
"""
from __future__ import annotations

import re
from typing import Optional


_NON_DIGITS = re.compile(r"\D+")


class InvalidPhoneError(ValueError):
    """Raised when a phone value cannot be normalized."""


def normalize_phone(raw: Optional[str], *, default_region: str = "IN") -> Optional[str]:
    """Return E.164 phone or None if input is empty.

    Accepts common Indian forms:
      9876543210, 09876543210, 919876543210, +91 98765 43210, 91-9876543210

    Raises InvalidPhoneError if non-empty but unusable.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    digits = _NON_DIGITS.sub("", text)
    if not digits:
        raise InvalidPhoneError(f"no digits in phone: {raw!r}")

    # Strip leading trunk 0 after removing country code attempts
    if digits.startswith("91") and len(digits) == 12:
        national = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        national = digits[1:]
    elif len(digits) == 10:
        national = digits
    elif text.startswith("+") and digits.startswith("91") and len(digits) == 12:
        national = digits[2:]
    else:
        raise InvalidPhoneError(f"unsupported phone format: {raw!r}")

    if len(national) != 10 or national[0] not in "6789":
        raise InvalidPhoneError(f"invalid Indian mobile number: {raw!r}")

    if default_region != "IN":
        raise InvalidPhoneError(f"unsupported region: {default_region}")

    return f"+91{national}"


def phones_match(a: Optional[str], b: Optional[str]) -> bool:
    """True if both normalize to the same E.164 value."""
    try:
        na, nb = normalize_phone(a), normalize_phone(b)
    except InvalidPhoneError:
        return False
    return na is not None and na == nb
