"""
Step 10 — Pilot helpers: revenue windows, accepted-action outcomes,
historical action ↔ revenue-lift correlation.
"""
from __future__ import annotations

import calendar
import functools
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

import yaml
from sqlalchemy import func

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "pilot.yaml",
)


@functools.lru_cache(maxsize=None)
def load_pilot_config(path: Optional[str] = None) -> dict:
    cfg_path = path or CONFIG_PATH
    with open(cfg_path) as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("pilot", raw) or {}


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _add_months(d: date, n: int) -> date:
    """First day of the month that is n months after d's month."""
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def revenue_window(
    session,
    store_id: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    """
    Sum store revenue for [start, end] inclusive.
    Prefers daily_revenue_facts; falls back to live invoices.
    """
    from db.models import DailyRevenueFact, Invoice

    facts = (
        session.query(
            func.coalesce(func.sum(DailyRevenueFact.revenue), 0),
            func.coalesce(func.sum(DailyRevenueFact.transactions), 0),
            func.count(DailyRevenueFact.fact_id),
        )
        .filter(
            DailyRevenueFact.store_id == store_id,
            DailyRevenueFact.fact_date.between(start, end),
        )
        .one()
    )
    fact_rev, fact_txns, fact_n = facts
    if int(fact_n or 0) > 0:
        rev = float(fact_rev or 0)
        txns = int(fact_txns or 0)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "revenue": round(rev, 2),
            "transactions": txns,
            "avg_basket_value": round(rev / txns, 2) if txns else 0.0,
            "source": "daily_revenue_facts",
        }

    row = (
        session.query(
            func.coalesce(func.sum(Invoice.grand_total), 0),
            func.count(func.distinct(Invoice.invoice_id)),
        )
        .filter(
            Invoice.store_id == store_id,
            Invoice.invoice_date.between(start, end),
        )
        .one()
    )
    rev = float(row[0] or 0)
    txns = int(row[1] or 0)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "revenue": round(rev, 2),
        "transactions": txns,
        "avg_basket_value": round(rev / txns, 2) if txns else 0.0,
        "source": "invoices",
    }


def _pct_change(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def accepted_action_outcomes(
    session,
    store_id: str,
    *,
    baseline_days: Optional[int] = None,
    followup_days: Optional[int] = None,
    as_of: Optional[date] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    For each accepted/done insight with resolved_at, compare revenue in the
    baseline window before resolve vs follow-up window after.
    """
    from db.models import Insight

    cfg = load_pilot_config()
    baseline_days = int(baseline_days if baseline_days is not None else cfg.get("baseline_days", 30))
    followup_days = int(followup_days if followup_days is not None else cfg.get("followup_days", 30))
    acted = set(cfg.get("acted_statuses") or ["accepted", "done"])
    min_elapsed = int(cfg.get("min_followup_elapsed_days", 7))
    as_of = as_of or date.today()

    rows = (
        session.query(Insight)
        .filter(
            Insight.store_id == store_id,
            Insight.status.in_(list(acted)),
            Insight.resolved_at.isnot(None),
        )
        .order_by(Insight.resolved_at.desc())
        .limit(limit)
        .all()
    )

    outcomes: list[dict[str, Any]] = []
    for r in rows:
        resolved = r.resolved_at
        if isinstance(resolved, datetime):
            resolved_d = resolved.date()
        else:
            resolved_d = resolved

        baseline_end = resolved_d - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=baseline_days - 1)
        follow_start = resolved_d
        follow_end = resolved_d + timedelta(days=followup_days - 1)

        elapsed = (as_of - resolved_d).days
        measurable = elapsed >= min_elapsed
        # Cap follow-up at as_of so we don't invent future revenue
        effective_follow_end = min(follow_end, as_of)

        baseline = revenue_window(session, store_id, baseline_start, baseline_end)
        followup = (
            revenue_window(session, store_id, follow_start, effective_follow_end)
            if measurable and effective_follow_end >= follow_start
            else None
        )

        delta = None
        pct = None
        if followup is not None:
            delta = round(followup["revenue"] - baseline["revenue"], 2)
            pct = _pct_change(followup["revenue"], baseline["revenue"])

        outcomes.append(
            {
                "insight_id": r.insight_id,
                "action_type": r.action_type,
                "priority": r.priority,
                "title": r.title,
                "status": r.status,
                "feedback": r.feedback,
                "resolved_at": resolved_d.isoformat(),
                "impact_estimate": float(r.impact_estimate) if r.impact_estimate is not None else None,
                "baseline_days": baseline_days,
                "followup_days": followup_days,
                "days_since_resolve": elapsed,
                "measurable": measurable,
                "baseline": baseline,
                "followup": followup,
                "revenue_delta": delta,
                "revenue_pct_change": pct,
            }
        )
    return outcomes


def summarize_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate accepted-action outcomes into pilot summary stats."""
    measurable = [o for o in outcomes if o.get("measurable") and o.get("followup")]
    lifts = [o["revenue_pct_change"] for o in measurable if o.get("revenue_pct_change") is not None]
    positive = sum(1 for x in lifts if x > 0)
    negative = sum(1 for x in lifts if x < 0)
    by_type: dict[str, dict[str, Any]] = {}
    for o in measurable:
        at = o.get("action_type") or "unknown"
        bucket = by_type.setdefault(
            at, {"n": 0, "avg_pct_change": None, "pct_changes": []}
        )
        bucket["n"] += 1
        if o.get("revenue_pct_change") is not None:
            bucket["pct_changes"].append(o["revenue_pct_change"])

    for bucket in by_type.values():
        vals = bucket.pop("pct_changes")
        bucket["avg_pct_change"] = (
            round(sum(vals) / len(vals), 2) if vals else None
        )

    return {
        "n_acted": len(outcomes),
        "n_measurable": len(measurable),
        "n_with_lift": len(lifts),
        "n_positive_lift": positive,
        "n_negative_lift": negative,
        "avg_revenue_pct_change": round(sum(lifts) / len(lifts), 2) if lifts else None,
        "by_action_type": by_type,
    }


def historical_action_correlation(
    session,
    store_id: str,
    *,
    min_months: Optional[int] = None,
) -> dict[str, Any]:
    """
    For each calendar month that has tagged insights, look at next-month
    revenue lift and whether any action was accepted in that month.

    Does not prove causality — reports correlation for pilot tuning.
    """
    from db.models import Insight, MonthlyRevenueFact

    cfg = load_pilot_config()
    corr_cfg = cfg.get("correlation") or {}
    min_months = int(min_months if min_months is not None else corr_cfg.get("min_months", 2))
    high_only = bool(corr_cfg.get("high_priority_only", False))
    acted = set(cfg.get("acted_statuses") or ["accepted", "done"])

    # Prefer monthly facts; also support invoice-derived months via insights periods
    months = (
        session.query(MonthlyRevenueFact)
        .filter(MonthlyRevenueFact.store_id == store_id)
        .order_by(MonthlyRevenueFact.year, MonthlyRevenueFact.month)
        .all()
    )

    month_map: dict[tuple[int, int], Any] = {(m.year, m.month): m for m in months}

    # Insights grouped by period month (period_start) or generated_at month
    insights = (
        session.query(Insight)
        .filter(Insight.store_id == store_id)
        .all()
    )

    by_month: dict[tuple[int, int], list] = {}
    for ins in insights:
        if ins.period_start is not None:
            key = (ins.period_start.year, ins.period_start.month)
        elif ins.generated_at is not None:
            ga = ins.generated_at
            if isinstance(ga, datetime):
                key = (ga.year, ga.month)
            else:
                key = (ga.year, ga.month)
        else:
            continue
        by_month.setdefault(key, []).append(ins)

    # Build month keys from facts ∪ insight months
    keys = sorted(set(month_map.keys()) | set(by_month.keys()))
    rows_out: list[dict[str, Any]] = []

    for y, m in keys:
        next_start = _add_months(date(y, m, 1), 1)
        next_key = (next_start.year, next_start.month)
        if next_key not in month_map and next_key not in by_month:
            # Still try revenue_window for next month if we have any data
            pass

        ps, pe = _month_bounds(y, m)
        ns, ne = _month_bounds(next_start.year, next_start.month)

        if (y, m) in month_map and next_key in month_map:
            cur_rev = float(month_map[(y, m)].revenue or 0)
            next_rev = float(month_map[next_key].revenue or 0)
            source = "monthly_revenue_facts"
        else:
            cur = revenue_window(session, store_id, ps, pe)
            nxt = revenue_window(session, store_id, ns, ne)
            cur_rev = cur["revenue"]
            next_rev = nxt["revenue"]
            source = cur["source"]
            if cur_rev == 0 and next_rev == 0 and (y, m) not in by_month:
                continue

        month_insights = by_month.get((y, m), [])
        if high_only:
            relevant = [i for i in month_insights if (i.priority or "").lower() == "high"]
        else:
            relevant = month_insights

        n_accepted = sum(1 for i in relevant if (i.status or "") in acted)
        n_dismissed = sum(1 for i in relevant if (i.status or "") == "dismissed")
        n_useful = sum(
            1 for i in relevant if (i.feedback or "").strip().lower() == "useful"
        )
        acted_flag = n_accepted > 0

        rows_out.append(
            {
                "year": y,
                "month": m,
                "period_start": ps.isoformat(),
                "period_end": pe.isoformat(),
                "revenue": round(cur_rev, 2),
                "next_month_revenue": round(next_rev, 2),
                "next_month_pct_change": _pct_change(next_rev, cur_rev),
                "n_insights": len(month_insights),
                "n_accepted": n_accepted,
                "n_dismissed": n_dismissed,
                "n_useful": n_useful,
                "acted": acted_flag,
                "source": source,
            }
        )

    with_next = [r for r in rows_out if r.get("next_month_pct_change") is not None]
    acted_rows = [r for r in with_next if r["acted"]]
    idle_rows = [r for r in with_next if not r["acted"]]

    def _avg_lift(group: list[dict]) -> Optional[float]:
        vals = [r["next_month_pct_change"] for r in group if r["next_month_pct_change"] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    enough = len(with_next) >= min_months
    return {
        "store_id": store_id,
        "enough_history": enough,
        "min_months": min_months,
        "n_months": len(rows_out),
        "n_months_with_next": len(with_next),
        "avg_next_month_pct_when_acted": _avg_lift(acted_rows),
        "avg_next_month_pct_when_idle": _avg_lift(idle_rows),
        "n_acted_months": len(acted_rows),
        "n_idle_months": len(idle_rows),
        "lift_gap_pp": (
            round(
                (_avg_lift(acted_rows) or 0) - (_avg_lift(idle_rows) or 0),
                2,
            )
            if acted_rows and idle_rows
            else None
        ),
        "months": rows_out,
        "note": (
            "Correlation only — accepted actions may coincide with busy months; "
            "use with accepted_action_outcomes for per-action before/after."
        ),
    }


def pilot_summary(
    session,
    store_id: str,
    *,
    baseline_days: Optional[int] = None,
    followup_days: Optional[int] = None,
) -> dict[str, Any]:
    """Combined pilot dashboard payload."""
    from db.learning import action_outcome_summary

    outcomes = accepted_action_outcomes(
        session,
        store_id,
        baseline_days=baseline_days,
        followup_days=followup_days,
    )
    return {
        "store_id": store_id,
        "config": {
            "baseline_days": baseline_days
            if baseline_days is not None
            else load_pilot_config().get("baseline_days", 30),
            "followup_days": followup_days
            if followup_days is not None
            else load_pilot_config().get("followup_days", 30),
        },
        "feedback_tallies": action_outcome_summary(session, store_id),
        "outcomes": summarize_outcomes(outcomes),
        "outcome_rows": outcomes[:20],
        "correlation": historical_action_correlation(session, store_id),
        "suggested_festival_overrides": load_pilot_config().get(
            "suggested_festival_overrides", {}
        ),
    }
