"""Currency/number cleanup: strip symbols, handle locale differences,
coerce to a clean Decimal-compatible float. Never guesses a value --
returns None if it genuinely can't parse, so the caller can decide
whether that's fatal for a given field."""
import re
from typing import Optional

_CURRENCY_SYMBOLS = re.compile(r"[₹$€£¥,\s]")


def clean_number(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text == "":
        return None

    # Handle parentheses-as-negative accounting format, e.g. "(120.00)"
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")

    text = _CURRENCY_SYMBOLS.sub("", text)
    if text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value
