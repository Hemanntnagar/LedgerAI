"""
Seed Indian festival calendar into festival_events.

Dates for lunar festivals are approximate civic/retail planning windows
(stock-up windows often start 1–2 weeks before the peak day). Refine
with an official calendar feed in a later step if needed.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, List, Tuple

from db.models import FestivalEvent
from db.session import get_session


# (name, year, start, end, category, notes)
FestivalRow = Tuple[str, int, date, date, str, str]

_SEED: List[FestivalRow] = [
    # 2025
    ("Republic Day", 2025, date(2025, 1, 26), date(2025, 1, 26), "national", "Fixed"),
    ("Pongal / Makar Sankranti", 2025, date(2025, 1, 14), date(2025, 1, 17), "regional", "South / harvest"),
    ("Holi", 2025, date(2025, 3, 13), date(2025, 3, 14), "religious", "Approx lunar"),
    ("Eid ul-Fitr", 2025, date(2025, 3, 30), date(2025, 3, 31), "religious", "Approx lunar"),
    ("Independence Day", 2025, date(2025, 8, 15), date(2025, 8, 15), "national", "Fixed"),
    ("Raksha Bandhan", 2025, date(2025, 8, 9), date(2025, 8, 9), "religious", "Approx lunar"),
    ("Janmashtami", 2025, date(2025, 8, 16), date(2025, 8, 16), "religious", "Approx lunar"),
    ("Ganesh Chaturthi", 2025, date(2025, 8, 27), date(2025, 9, 6), "religious", "Approx window"),
    ("Onam", 2025, date(2025, 9, 5), date(2025, 9, 14), "regional", "Kerala"),
    ("Navratri", 2025, date(2025, 9, 22), date(2025, 10, 1), "religious", "Approx lunar"),
    ("Dussehra", 2025, date(2025, 10, 2), date(2025, 10, 2), "religious", "Approx lunar"),
    ("Diwali", 2025, date(2025, 10, 18), date(2025, 10, 23), "religious", "Peak retail window"),
    ("Eid ul-Adha", 2025, date(2025, 6, 6), date(2025, 6, 7), "religious", "Approx lunar"),
    ("Christmas", 2025, date(2025, 12, 24), date(2025, 12, 25), "religious", "Fixed"),
    # 2026
    ("Republic Day", 2026, date(2026, 1, 26), date(2026, 1, 26), "national", "Fixed"),
    ("Pongal / Makar Sankranti", 2026, date(2026, 1, 14), date(2026, 1, 17), "regional", "South / harvest"),
    ("Holi", 2026, date(2026, 3, 3), date(2026, 3, 4), "religious", "Approx lunar"),
    ("Eid ul-Fitr", 2026, date(2026, 3, 20), date(2026, 3, 21), "religious", "Approx lunar"),
    ("Eid ul-Adha", 2026, date(2026, 5, 27), date(2026, 5, 28), "religious", "Approx lunar"),
    ("Independence Day", 2026, date(2026, 8, 15), date(2026, 8, 15), "national", "Fixed"),
    ("Raksha Bandhan", 2026, date(2026, 8, 28), date(2026, 8, 28), "religious", "Approx lunar"),
    ("Janmashtami", 2026, date(2026, 9, 4), date(2026, 9, 4), "religious", "Approx lunar"),
    ("Ganesh Chaturthi", 2026, date(2026, 9, 14), date(2026, 9, 24), "religious", "Approx window"),
    ("Onam", 2026, date(2026, 8, 26), date(2026, 9, 4), "regional", "Kerala"),
    ("Navratri", 2026, date(2026, 10, 11), date(2026, 10, 20), "religious", "Approx lunar"),
    ("Dussehra", 2026, date(2026, 10, 20), date(2026, 10, 20), "religious", "Approx lunar"),
    ("Diwali", 2026, date(2026, 11, 5), date(2026, 11, 12), "religious", "Peak retail window"),
    ("Christmas", 2026, date(2026, 12, 24), date(2026, 12, 25), "religious", "Fixed"),
    # 2027 (planning ahead)
    ("Republic Day", 2027, date(2027, 1, 26), date(2027, 1, 26), "national", "Fixed"),
    ("Holi", 2027, date(2027, 3, 22), date(2027, 3, 23), "religious", "Approx lunar"),
    ("Independence Day", 2027, date(2027, 8, 15), date(2027, 8, 15), "national", "Fixed"),
    ("Diwali", 2027, date(2027, 10, 25), date(2027, 11, 1), "religious", "Peak retail window"),
    ("Christmas", 2027, date(2027, 12, 24), date(2027, 12, 25), "religious", "Fixed"),
]


def iter_seed_rows() -> Iterable[FestivalRow]:
    return iter(_SEED)


def seed_festival_events(session=None, *, region: str = "india") -> int:
    """Insert missing festival rows. Returns count of newly inserted rows."""
    own_session = session is None
    if own_session:
        session = get_session()
    inserted = 0
    try:
        for name, year, start, end, category, notes in _SEED:
            exists = (
                session.query(FestivalEvent)
                .filter_by(name=name, year=year, region=region)
                .first()
            )
            if exists:
                continue
            session.add(
                FestivalEvent(
                    name=name,
                    year=year,
                    start_date=start,
                    end_date=end,
                    region=region,
                    category=category,
                    notes=notes,
                )
            )
            inserted += 1
        session.commit()
        return inserted
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()
