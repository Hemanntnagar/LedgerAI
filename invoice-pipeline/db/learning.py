"""
Step 7 — Month-over-month learning helpers for report snapshots.

- MAPE summary from forecast_results
- Previous-period snapshot lookup
- MoM KPI deltas + prior-period action outcome tallies
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional


def build_mape_summary(session, store_id: str) -> dict[str, Any]:
    """
    Aggregate backtest MAPE from the latest forecast_results for a store.
    Returns empty-ish dict when ML tables are missing or empty.
    """
    try:
        from common.result_models import ForecastResult
    except ImportError:
        return {"available": False, "reason": "forecast_results model not loaded"}

    rows = (
        session.query(ForecastResult)
        .filter(ForecastResult.store_id == store_id)
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no forecast_results", "n_rows": 0}

    mapes = [
        float(r.backtest_mape_pct)
        for r in rows
        if r.backtest_mape_pct is not None
    ]
    by_conf: dict[str, int] = {}
    versions: dict[str, int] = {}
    for r in rows:
        lvl = (r.confidence_level or "unknown").lower()
        by_conf[lvl] = by_conf.get(lvl, 0) + 1
        ver = r.model_version or "unknown"
        versions[ver] = versions.get(ver, 0) + 1

    avg_mape = round(sum(mapes) / len(mapes), 2) if mapes else None
    return {
        "available": True,
        "n_rows": len(rows),
        "n_with_mape": len(mapes),
        "avg_mape_pct": avg_mape,
        "min_mape_pct": round(min(mapes), 2) if mapes else None,
        "max_mape_pct": round(max(mapes), 2) if mapes else None,
        "by_confidence": by_conf,
        "model_versions": versions,
    }


def find_previous_snapshot(
    session,
    store_id: str,
    period_start: date,
    *,
    period_type: str = "monthly",
):
    """Latest snapshot for this store whose period ends before current period_start."""
    from db.models import ReportSnapshot

    return (
        session.query(ReportSnapshot)
        .filter(
            ReportSnapshot.store_id == store_id,
            ReportSnapshot.period_type == period_type,
            ReportSnapshot.period_end < period_start,
        )
        .order_by(ReportSnapshot.period_end.desc())
        .first()
    )


def _kpi_float(kpis: Optional[dict], key: str) -> Optional[float]:
    if not kpis:
        return None
    val = kpis.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _pct_change(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def mom_kpi_deltas(
    current_kpis: Optional[dict],
    previous_kpis: Optional[dict],
) -> dict[str, Any]:
    """Compare KPI dicts between consecutive report snapshots."""
    keys = (
        "total_revenue",
        "total_transactions",
        "avg_basket_value",
        "units_sold",
    )
    deltas: dict[str, Any] = {}
    for key in keys:
        curr = _kpi_float(current_kpis, key)
        prev = _kpi_float(previous_kpis, key)
        delta = None if curr is None or prev is None else round(curr - prev, 2)
        deltas[key] = {
            "current": curr,
            "previous": prev,
            "delta": delta,
            "pct_change": _pct_change(curr, prev),
        }
    return deltas


def action_outcome_summary(
    session,
    store_id: str,
    *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> dict[str, Any]:
    """
    Tally insight feedback for a store (optionally scoped to a report period).
    Used for MoM 'what happened to last month's actions'.
    """
    from db.models import Insight

    q = session.query(Insight).filter(Insight.store_id == store_id)
    if period_start is not None:
        q = q.filter(Insight.period_start == period_start)
    if period_end is not None:
        q = q.filter(Insight.period_end == period_end)

    rows = q.all()
    useful = not_useful = no_feedback = 0
    by_type: dict[str, dict[str, int]] = {}
    status_counts: dict[str, int] = {}

    for r in rows:
        at = r.action_type or "unknown"
        bucket = by_type.setdefault(at, {"useful": 0, "not_useful": 0, "none": 0})
        fb = (r.feedback or "").strip().lower() if r.feedback else None
        if fb == "useful":
            useful += 1
            bucket["useful"] += 1
        elif fb == "not_useful":
            not_useful += 1
            bucket["not_useful"] += 1
        else:
            no_feedback += 1
            bucket["none"] += 1
        st = r.status or "unknown"
        status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "n_insights": len(rows),
        "useful": useful,
        "not_useful": not_useful,
        "no_feedback": no_feedback,
        "by_action_type": by_type,
        "by_status": status_counts,
    }


def build_mom_comparison(
    session,
    store_id: str,
    *,
    period_start: date,
    period_end: date,
    period_type: str,
    current_kpis: Optional[dict],
) -> Optional[dict[str, Any]]:
    """Full MoM block for a newly generated (or listed) report."""
    from db.models import ReportSnapshot

    prev = find_previous_snapshot(
        session, store_id, period_start, period_type=period_type
    )
    if not prev:
        return None

    outcomes = action_outcome_summary(
        session,
        store_id,
        period_start=prev.period_start,
        period_end=prev.period_end,
    )
    history_n = (
        session.query(ReportSnapshot)
        .filter_by(store_id=store_id, period_type=period_type)
        .count()
    )
    return {
        "previous_snapshot_id": prev.snapshot_id,
        "previous_period_start": prev.period_start.isoformat(),
        "previous_period_end": prev.period_end.isoformat(),
        "kpi_deltas": mom_kpi_deltas(current_kpis, prev.kpis_json),
        "previous_action_outcomes": outcomes,
        # +1 for the snapshot being generated (not yet flushed when called mid-create)
        "history_months": history_n + 1,
    }
