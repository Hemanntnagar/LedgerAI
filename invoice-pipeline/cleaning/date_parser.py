"""Date/time standardization. Tries a fixed list of common invoice date
formats and returns a Python date/time object, or None if nothing matched
-- we never guess a date, since a wrong date silently corrupts every
downstream time-series analysis."""
from datetime import datetime, date, time
from typing import Optional

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
]

TIME_FORMATS = [
    "%H:%M:%S",
    "%H:%M",
    "%I:%M %p",
    "%I:%M:%S %p",
]


def parse_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(raw: Optional[str]) -> Optional[time]:
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None
