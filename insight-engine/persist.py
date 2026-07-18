"""Persist ranked actions into the shared `insights` table."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from templates import render_action


def persist_insights(
    session,
    store_id: str,
    scored_rows: list[dict],
    *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    replace_status: str = "new",
) -> list:
    """
    Delete existing insights with status=replace_status for this store,
    then insert the new ranked list. Accepted/dismissed/done are kept.
    """
    from db.models import Insight

    if replace_status:
        (
            session.query(Insight)
            .filter(
                Insight.store_id == store_id,
                Insight.status == replace_status,
            )
            .delete(synchronize_session=False)
        )

    created = []
    now = datetime.utcnow()
    for row in scored_rows:
        title, body = render_action(row)
        insight = Insight(
            store_id=store_id,
            action_type=row["action_type"],
            priority=row["priority"],
            title=title,
            body=body,
            impact_estimate=row["impact_estimate"],
            confidence=row["confidence"],
            urgency=row["urgency"],
            score=row["score"],
            evidence_json=row.get("evidence"),
            source_models=row.get("source_models"),
            status="new",
            period_start=period_start,
            period_end=period_end,
            generated_at=now,
        )
        session.add(insight)
        created.append(insight)

    session.flush()
    return created
