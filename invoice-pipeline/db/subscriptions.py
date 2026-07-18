"""
Subscription lifecycle helpers: period rollover + report quota (Step 2).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple

from db.models import ReportSnapshot, Subscription, gen_uuid


@dataclass
class QuotaCheck:
    allowed: bool
    reason: Optional[str] = None
    reports_used: int = 0
    monthly_report_quota: int = 0
    period_start: Optional[date] = None
    period_end: Optional[date] = None


def _month_bounds(on: date) -> Tuple[date, date]:
    start = on.replace(day=1)
    last_day = calendar.monthrange(on.year, on.month)[1]
    end = on.replace(day=last_day)
    return start, end


def ensure_billing_period(sub: Subscription, today: Optional[date] = None) -> Subscription:
    """
    Reset report counters when the calendar month rolls over.
    period_start/period_end define the current quota window.
    """
    today = today or date.today()
    start, end = _month_bounds(today)
    if sub.period_start != start or sub.period_end != end:
        sub.period_start = start
        sub.period_end = end
        sub.reports_used_this_period = 0
    return sub


def check_report_quota(sub: Subscription, today: Optional[date] = None) -> QuotaCheck:
    """Return whether generating a report is allowed under the current plan."""
    today = today or date.today()
    ensure_billing_period(sub, today)

    if sub.status != "active":
        return QuotaCheck(
            allowed=False,
            reason=f"subscription status is '{sub.status}' (need active)",
            reports_used=sub.reports_used_this_period,
            monthly_report_quota=sub.monthly_report_quota,
            period_start=sub.period_start,
            period_end=sub.period_end,
        )

    if sub.reports_used_this_period >= sub.monthly_report_quota:
        return QuotaCheck(
            allowed=False,
            reason=(
                f"monthly report quota exhausted "
                f"({sub.reports_used_this_period}/{sub.monthly_report_quota})"
            ),
            reports_used=sub.reports_used_this_period,
            monthly_report_quota=sub.monthly_report_quota,
            period_start=sub.period_start,
            period_end=sub.period_end,
        )

    return QuotaCheck(
        allowed=True,
        reports_used=sub.reports_used_this_period,
        monthly_report_quota=sub.monthly_report_quota,
        period_start=sub.period_start,
        period_end=sub.period_end,
    )


def consume_report_quota(sub: Subscription, today: Optional[date] = None) -> QuotaCheck:
    """Increment usage after a successful report generation. Raises if not allowed."""
    check = check_report_quota(sub, today)
    if not check.allowed:
        raise PermissionError(check.reason or "report quota denied")
    sub.reports_used_this_period = int(sub.reports_used_this_period or 0) + 1
    sub.updated_at = datetime.utcnow()
    return check_report_quota(sub, today)


def create_report_snapshot(
    session,
    *,
    store_id: str,
    period_start: date,
    period_end: date,
    period_type: str = "monthly",
    kpis: Optional[dict] = None,
    top_actions: Optional[list] = None,
    model_versions: Optional[dict] = None,
    mape_summary: Optional[dict] = None,
) -> ReportSnapshot:
    """Persist a report snapshot for MoM learning (Step 7)."""
    existing = (
        session.query(ReportSnapshot)
        .filter_by(
            store_id=store_id,
            period_start=period_start,
            period_end=period_end,
            period_type=period_type,
        )
        .first()
    )
    if existing:
        existing.kpis_json = kpis
        existing.top_actions_json = top_actions
        if model_versions is not None:
            existing.model_versions_json = model_versions
        if mape_summary is not None:
            existing.mape_summary_json = mape_summary
        existing.generated_at = datetime.utcnow()
        session.flush()
        return existing

    snap = ReportSnapshot(
        snapshot_id=gen_uuid(),
        store_id=store_id,
        period_start=period_start,
        period_end=period_end,
        period_type=period_type,
        kpis_json=kpis,
        top_actions_json=top_actions,
        model_versions_json=model_versions,
        mape_summary_json=mape_summary,
    )
    session.add(snap)
    session.flush()
    return snap
