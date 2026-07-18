"""
All aggregation queries live here, one function per metric group. Every
function does its grouping/summing in SQL (not in Python/pandas) for
performance, and accepts an already-open SQLAlchemy session plus filter
params so it's easily unit-testable.
"""
import calendar
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from dbconn import Store, Product, Invoice, InvoiceLineItem

# Insight / revenue-fact models live in the shared pipeline schema.
try:
    from db.models import Insight, DailyRevenueFact, MonthlyRevenueFact, ReportSnapshot
except ImportError:  # pragma: no cover
    Insight = None
    DailyRevenueFact = None
    MonthlyRevenueFact = None
    ReportSnapshot = None

try:
    from db.learning import build_mom_comparison
except ImportError:  # pragma: no cover
    build_mom_comparison = None

try:
    from db.revenue_facts import aggregate_weekly_from_daily
except ImportError:  # pragma: no cover
    aggregate_weekly_from_daily = None


def _apply_store_filter(query, store_id: Optional[str]):
    if store_id and store_id != "all":
        query = query.filter(Invoice.store_id == store_id)
    return query


def get_kpis(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None) -> dict:
    q = session.query(
        func.coalesce(func.sum(Invoice.grand_total), 0).label("revenue"),
        func.count(func.distinct(Invoice.invoice_id)).label("transactions"),
    ).filter(Invoice.invoice_date.between(start_date, end_date))
    q = _apply_store_filter(q, store_id)
    revenue, transactions = q.one()

    units_q = (
        session.query(func.coalesce(func.sum(InvoiceLineItem.quantity), 0))
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    units_q = _apply_store_filter(units_q, store_id)
    units_sold = units_q.scalar() or 0

    # previous period of equal length, immediately preceding start_date
    period_len = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_len - 1)

    prev_q = session.query(func.coalesce(func.sum(Invoice.grand_total), 0)).filter(
        Invoice.invoice_date.between(prev_start, prev_end)
    )
    prev_q = _apply_store_filter(prev_q, store_id)
    prev_revenue = prev_q.scalar() or 0

    revenue = float(revenue)
    prev_revenue = float(prev_revenue)
    pct_change = ((revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else None

    avg_basket = (revenue / transactions) if transactions else 0

    return {
        "total_revenue": round(revenue, 2),
        "total_transactions": transactions,
        "avg_basket_value": round(avg_basket, 2),
        "units_sold": float(units_sold),
        "revenue_pct_change_vs_previous_period": round(pct_change, 2) if pct_change is not None else None,
    }


def get_sales_trend(
    session: Session,
    start_date: date,
    end_date: date,
    store_id: Optional[str] = None,
    granularity: str = "day",
) -> list:
    if granularity == "week":
        bucket = func.strftime("%Y-W%W", Invoice.invoice_date)
    elif granularity == "month":
        bucket = func.strftime("%Y-%m", Invoice.invoice_date)
    else:
        bucket = func.strftime("%Y-%m-%d", Invoice.invoice_date)

    q = (
        session.query(bucket.label("period"), func.sum(Invoice.grand_total).label("revenue"))
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by("period").order_by("period")
    rows = q.all()

    series = [{"period": r.period, "revenue": round(float(r.revenue or 0), 2)} for r in rows]

    window = 7
    for i, point in enumerate(series):
        lo = max(0, i - window + 1)
        window_vals = [p["revenue"] for p in series[lo : i + 1]]
        point["moving_avg_7"] = round(sum(window_vals) / len(window_vals), 2)

    return series


def _live_revenue_series(
    session: Session,
    start_date: date,
    end_date: date,
    store_id: Optional[str],
    granularity: str,
) -> list:
    """Live SQL aggregates with revenue + transactions + units (for /api/revenue)."""
    if granularity == "week":
        q = session.query(
            Invoice.invoice_date,
            Invoice.invoice_id,
            Invoice.grand_total,
        ).filter(Invoice.invoice_date.between(start_date, end_date))
        q = _apply_store_filter(q, store_id)
        invoices = q.all()

        units_by_inv = {}
        if invoices:
            inv_ids = [i.invoice_id for i in invoices]
            urows = (
                session.query(
                    InvoiceLineItem.invoice_id,
                    func.coalesce(func.sum(InvoiceLineItem.quantity), 0),
                )
                .filter(InvoiceLineItem.invoice_id.in_(inv_ids))
                .group_by(InvoiceLineItem.invoice_id)
                .all()
            )
            units_by_inv = {uid: float(u or 0) for uid, u in urows}

        buckets: dict[str, dict] = {}
        for inv in invoices:
            iso = inv.invoice_date.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            ws = inv.invoice_date - timedelta(days=inv.invoice_date.weekday())
            if key not in buckets:
                buckets[key] = {
                    "period": key,
                    "period_start": ws.isoformat(),
                    "revenue": 0.0,
                    "transactions": 0,
                    "units_sold": 0.0,
                    "_seen": set(),
                }
            b = buckets[key]
            if inv.invoice_id not in b["_seen"]:
                b["_seen"].add(inv.invoice_id)
                b["transactions"] += 1
                b["revenue"] += float(inv.grand_total or 0)
                b["units_sold"] += units_by_inv.get(inv.invoice_id, 0.0)

        results = []
        for key in sorted(buckets.keys()):
            b = buckets[key]
            txns = b["transactions"]
            rev = b["revenue"]
            results.append(
                {
                    "period": b["period"],
                    "period_start": b["period_start"],
                    "revenue": round(rev, 2),
                    "transactions": txns,
                    "units_sold": round(b["units_sold"], 2),
                    "avg_basket_value": round(rev / txns, 2) if txns else 0.0,
                }
            )
        return results

    if granularity == "month":
        bucket = func.strftime("%Y-%m", Invoice.invoice_date)
    else:
        bucket = func.strftime("%Y-%m-%d", Invoice.invoice_date)

    q = (
        session.query(
            bucket.label("period"),
            func.coalesce(func.sum(Invoice.grand_total), 0).label("revenue"),
            func.count(func.distinct(Invoice.invoice_id)).label("transactions"),
        )
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by("period").order_by("period")
    rows = q.all()

    results = []
    for r in rows:
        period = r.period
        if granularity == "month":
            y, m = int(period[:4]), int(period[5:7])
            p_start = date(y, m, 1)
            p_end = date(y, m, calendar.monthrange(y, m)[1])
        else:
            p_start = date.fromisoformat(period)
            p_end = p_start

        units_q = (
            session.query(func.coalesce(func.sum(InvoiceLineItem.quantity), 0))
            .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
            .filter(Invoice.invoice_date.between(p_start, p_end))
        )
        units_q = _apply_store_filter(units_q, store_id)
        units = float(units_q.scalar() or 0)
        rev = float(r.revenue or 0)
        txns = int(r.transactions or 0)
        results.append(
            {
                "period": period,
                "period_start": p_start.isoformat(),
                "revenue": round(rev, 2),
                "transactions": txns,
                "units_sold": round(units, 2),
                "avg_basket_value": round(rev / txns, 2) if txns else 0.0,
            }
        )
    return results


def _facts_revenue_series(
    session: Session,
    start_date: date,
    end_date: date,
    store_id: Optional[str],
    granularity: str,
) -> Optional[list]:
    """Read from persisted facts when available; None if tables empty / missing."""
    if DailyRevenueFact is None or MonthlyRevenueFact is None:
        return None

    if granularity == "day":
        q = session.query(DailyRevenueFact).filter(
            DailyRevenueFact.fact_date.between(start_date, end_date)
        )
        if store_id and store_id != "all":
            q = q.filter(DailyRevenueFact.store_id == store_id)
        rows = q.order_by(DailyRevenueFact.fact_date).all()
        if not rows:
            return None
        buckets: dict[str, dict] = {}
        for r in rows:
            key = r.fact_date.isoformat()
            if key not in buckets:
                buckets[key] = {
                    "period": key,
                    "period_start": key,
                    "revenue": 0.0,
                    "transactions": 0,
                    "units_sold": 0.0,
                }
            buckets[key]["revenue"] += float(r.revenue or 0)
            buckets[key]["transactions"] += int(r.transactions or 0)
            buckets[key]["units_sold"] += float(r.units_sold or 0)
        out = []
        for key in sorted(buckets.keys()):
            b = buckets[key]
            txns = b["transactions"]
            rev = b["revenue"]
            out.append(
                {
                    "period": b["period"],
                    "period_start": b["period_start"],
                    "revenue": round(rev, 2),
                    "transactions": txns,
                    "units_sold": round(b["units_sold"], 2),
                    "avg_basket_value": round(rev / txns, 2) if txns else 0.0,
                }
            )
        return out

    if granularity == "week":
        if aggregate_weekly_from_daily is None:
            return None
        has_any = (
            session.query(DailyRevenueFact.fact_id)
            .filter(DailyRevenueFact.fact_date.between(start_date, end_date))
            .first()
        )
        if not has_any:
            return None
        return aggregate_weekly_from_daily(
            session, start_date=start_date, end_date=end_date, store_id=store_id
        )

    if granularity == "month":
        q = session.query(MonthlyRevenueFact).filter(
            MonthlyRevenueFact.period_end >= start_date,
            MonthlyRevenueFact.period_start <= end_date,
        )
        if store_id and store_id != "all":
            q = q.filter(MonthlyRevenueFact.store_id == store_id)
        rows = q.order_by(MonthlyRevenueFact.year, MonthlyRevenueFact.month).all()
        if not rows:
            return None
        buckets: dict[str, dict] = {}
        for r in rows:
            key = f"{r.year:04d}-{r.month:02d}"
            single_store = bool(store_id and store_id != "all")
            if key not in buckets:
                buckets[key] = {
                    "period": key,
                    "period_start": r.period_start.isoformat(),
                    "revenue": 0.0,
                    "transactions": 0,
                    "units_sold": 0.0,
                    "by_category": list(r.by_category_json or []) if single_store else None,
                    "by_product": list(r.by_product_json or []) if single_store else None,
                }
            else:
                buckets[key]["by_category"] = None
                buckets[key]["by_product"] = None
            buckets[key]["revenue"] += float(r.revenue or 0)
            buckets[key]["transactions"] += int(r.transactions or 0)
            buckets[key]["units_sold"] += float(r.units_sold or 0)
        out = []
        for key in sorted(buckets.keys()):
            b = buckets[key]
            txns = b["transactions"]
            rev = b["revenue"]
            row = {
                "period": b["period"],
                "period_start": b["period_start"],
                "revenue": round(rev, 2),
                "transactions": txns,
                "units_sold": round(b["units_sold"], 2),
                "avg_basket_value": round(rev / txns, 2) if txns else 0.0,
            }
            if b.get("by_category") is not None:
                row["by_category"] = b["by_category"]
            if b.get("by_product") is not None:
                row["by_product"] = b["by_product"]
            out.append(row)
        return out

    return None


def get_revenue_by_period(
    session: Session,
    start_date: date,
    end_date: date,
    store_id: Optional[str] = None,
    granularity: str = "day",
    prefer_facts: bool = True,
) -> dict:
    """
    Day / week / month revenue series for the store owner.

    Prefers persisted facts when rolled up; always falls back to live SQL.
    Returns {granularity, source, series, totals}.
    """
    if granularity not in ("day", "week", "month"):
        raise ValueError("granularity must be day, week, or month")

    source = "live"
    series = None
    if prefer_facts:
        series = _facts_revenue_series(session, start_date, end_date, store_id, granularity)
        if series is not None:
            source = "facts"

    if series is None:
        series = _live_revenue_series(session, start_date, end_date, store_id, granularity)
        source = "live"

    window = 7 if granularity == "day" else (4 if granularity == "week" else 3)
    for i, point in enumerate(series):
        lo = max(0, i - window + 1)
        vals = [p["revenue"] for p in series[lo : i + 1]]
        point["moving_avg"] = round(sum(vals) / len(vals), 2)

    totals = {
        "revenue": round(sum(p["revenue"] for p in series), 2),
        "transactions": sum(p["transactions"] for p in series),
        "units_sold": round(sum(p["units_sold"] for p in series), 2),
    }
    if totals["transactions"]:
        totals["avg_basket_value"] = round(totals["revenue"] / totals["transactions"], 2)
    else:
        totals["avg_basket_value"] = 0.0

    return {
        "granularity": granularity,
        "source": source,
        "series": series,
        "totals": totals,
    }


def get_top_products(
    session: Session,
    start_date: date,
    end_date: date,
    store_id: Optional[str] = None,
    limit: int = 10,
    by: str = "revenue",
) -> list:
    q = (
        session.query(
            Product.product_id,
            Product.product_name,
            func.sum(InvoiceLineItem.line_total).label("revenue"),
            func.sum(InvoiceLineItem.quantity).label("units"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.product_id == Product.product_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by(Product.product_id, Product.product_name)
    order_col = "revenue" if by == "revenue" else "units"
    rows = q.all()

    total_revenue = sum(float(r.revenue or 0) for r in rows) or 1
    results = [
        {
            "product_id": r.product_id,
            "product_name": r.product_name,
            "revenue": round(float(r.revenue or 0), 2),
            "units": float(r.units or 0),
            "pct_of_total_revenue": round(float(r.revenue or 0) / total_revenue * 100, 2),
        }
        for r in rows
    ]
    results.sort(key=lambda r: r[order_col], reverse=True)
    return results[:limit]


def get_pareto(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None) -> dict:
    q = (
        session.query(
            Product.product_id,
            Product.product_name,
            func.sum(InvoiceLineItem.line_total).label("revenue"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.product_id == Product.product_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by(Product.product_id, Product.product_name)
    rows = sorted(q.all(), key=lambda r: float(r.revenue or 0), reverse=True)

    total_revenue = sum(float(r.revenue or 0) for r in rows) or 1
    cumulative = 0.0
    results = []
    cutoff_index = None
    for i, r in enumerate(rows):
        rev = float(r.revenue or 0)
        cumulative += rev
        cumulative_pct = cumulative / total_revenue * 100
        results.append({
            "rank": i + 1,
            "product_id": r.product_id,
            "product_name": r.product_name,
            "revenue": round(rev, 2),
            "cumulative_pct": round(cumulative_pct, 2),
        })
        if cutoff_index is None and cumulative_pct >= 80:
            cutoff_index = i + 1

    return {"products": results, "products_making_80pct_revenue": cutoff_index}


def get_category_breakdown(
    session: Session, start_date: date, end_date: date, store_id: Optional[str] = None
) -> list:
    q = (
        session.query(
            func.coalesce(Product.category, "Uncategorized").label("category"),
            func.sum(InvoiceLineItem.line_total).label("revenue"),
            func.sum(InvoiceLineItem.quantity).label("units"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.product_id == Product.product_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by("category")
    rows = q.all()
    return [
        {"category": r.category, "revenue": round(float(r.revenue or 0), 2), "units": float(r.units or 0)}
        for r in rows
    ]


def get_time_heatmap(
    session: Session, start_date: date, end_date: date, store_id: Optional[str] = None
) -> list:
    has_time = session.query(Invoice.invoice_time).filter(Invoice.invoice_time.isnot(None)).first()

    if has_time:
        q = (
            session.query(
                func.strftime("%w", Invoice.invoice_date).label("day_of_week"),
                func.strftime("%H", Invoice.invoice_time).label("hour"),
                func.sum(Invoice.grand_total).label("revenue"),
                func.count(Invoice.invoice_id).label("transactions"),
            )
            .filter(Invoice.invoice_date.between(start_date, end_date))
        )
        q = _apply_store_filter(q, store_id)
        q = q.group_by("day_of_week", "hour")
        rows = q.all()
        return [
            {
                "day_of_week": int(r.day_of_week),
                "hour": int(r.hour),
                "revenue": round(float(r.revenue or 0), 2),
                "transactions": r.transactions,
            }
            for r in rows
        ]

    q = (
        session.query(
            func.strftime("%w", Invoice.invoice_date).label("day_of_week"),
            func.sum(Invoice.grand_total).label("revenue"),
            func.count(Invoice.invoice_id).label("transactions"),
        )
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by("day_of_week")
    rows = q.all()
    return [
        {
            "day_of_week": int(r.day_of_week),
            "hour": None,
            "revenue": round(float(r.revenue or 0), 2),
            "transactions": r.transactions,
        }
        for r in rows
    ]


def get_store_comparison(session: Session, start_date: date, end_date: date) -> list:
    q = (
        session.query(
            Store.store_id,
            Store.store_name,
            func.sum(Invoice.grand_total).label("revenue"),
            func.count(func.distinct(Invoice.invoice_id)).label("transactions"),
        )
        .join(Invoice, Invoice.store_id == Store.store_id)
        .filter(Invoice.invoice_date.between(start_date, end_date))
        .group_by(Store.store_id, Store.store_name)
        .order_by(func.sum(Invoice.grand_total).desc())
    )
    rows = q.all()
    return [
        {
            "store_id": r.store_id,
            "store_name": r.store_name,
            "revenue": round(float(r.revenue or 0), 2),
            "transactions": r.transactions,
            "avg_basket_value": round(float(r.revenue or 0) / r.transactions, 2) if r.transactions else 0,
        }
        for r in rows
    ]


def get_payment_methods(
    session: Session, start_date: date, end_date: date, store_id: Optional[str] = None
) -> list:
    q = (
        session.query(
            func.coalesce(Invoice.payment_method, "Unknown").label("method"),
            func.sum(Invoice.grand_total).label("revenue"),
            func.count(Invoice.invoice_id).label("transactions"),
        )
        .filter(Invoice.invoice_date.between(start_date, end_date))
    )
    q = _apply_store_filter(q, store_id)
    q = q.group_by("method")
    rows = q.all()
    return [
        {"payment_method": r.method, "revenue": round(float(r.revenue or 0), 2), "transactions": r.transactions}
        for r in rows
    ]


def get_insights(
    session: Session,
    store_id: str,
    limit: int = 50,
    status: Optional[str] = None,
) -> list:
    """Ranked actions for one store only (Step 2 isolation / Step 6 engine)."""
    if Insight is None or not store_id or store_id == "all":
        return []
    q = session.query(Insight).filter(Insight.store_id == store_id)
    if status:
        q = q.filter(Insight.status == status)
    q = q.order_by(Insight.generated_at.desc())
    rows = q.limit(limit * 2).all() if limit else q.all()
    results = [
        {
            "insight_id": r.insight_id,
            "store_id": r.store_id,
            "action_type": r.action_type,
            "priority": r.priority,
            "title": r.title,
            "body": r.body,
            "impact_estimate": float(r.impact_estimate) if r.impact_estimate is not None else None,
            "confidence": float(r.confidence) if r.confidence is not None else None,
            "urgency": float(r.urgency) if r.urgency is not None else None,
            "score": float(r.score) if r.score is not None else None,
            "status": r.status,
            "feedback": r.feedback,
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        }
        for r in rows
    ]
    results.sort(key=lambda r: (r["score"] is not None, r["score"] or 0), reverse=True)
    return results[:limit]


def set_insight_feedback(
    session: Session,
    *,
    store_id: str,
    insight_id: str,
    feedback: Optional[str] = None,
    status: Optional[str] = None,
) -> Optional[dict]:
    """Update feedback / status on one insight (tenant-scoped)."""
    if Insight is None:
        return None
    row = (
        session.query(Insight)
        .filter(Insight.insight_id == insight_id, Insight.store_id == store_id)
        .first()
    )
    if not row:
        return None

    if feedback is not None:
        fb = feedback.strip().lower() if feedback else None
        if fb in ("", "null", "none", "clear"):
            fb = None
        if fb is not None and fb not in ("useful", "not_useful"):
            raise ValueError("feedback must be useful, not_useful, or null")
        row.feedback = fb

    if status is not None:
        st = status.strip().lower()
        if st not in ("new", "accepted", "dismissed", "done"):
            raise ValueError("status must be new|accepted|dismissed|done")
        row.status = st
        if st in ("accepted", "dismissed", "done"):
            from datetime import datetime
            row.resolved_at = datetime.utcnow()

    session.flush()
    return {
        "insight_id": row.insight_id,
        "action_type": row.action_type,
        "status": row.status,
        "feedback": row.feedback,
    }


def get_report_snapshots(
    session: Session,
    store_id: str,
    limit: int = 12,
) -> list:
    """Report snapshots newest-first, each with MoM vs prior period when available."""
    if ReportSnapshot is None or not store_id or store_id == "all":
        return []
    rows = (
        session.query(ReportSnapshot)
        .filter(ReportSnapshot.store_id == store_id)
        .order_by(ReportSnapshot.period_end.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        mom = None
        if build_mom_comparison is not None:
            mom = build_mom_comparison(
                session,
                store_id,
                period_start=r.period_start,
                period_end=r.period_end,
                period_type=r.period_type,
                current_kpis=r.kpis_json,
            )
            # history_months already counts existing rows; don't +1 on list
            if mom is not None:
                history_n = (
                    session.query(ReportSnapshot)
                    .filter_by(store_id=store_id, period_type=r.period_type)
                    .count()
                )
                mom = dict(mom)
                mom["history_months"] = history_n
        out.append({
            "snapshot_id": r.snapshot_id,
            "store_id": r.store_id,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "period_type": r.period_type,
            "kpis": r.kpis_json,
            "top_actions": r.top_actions_json,
            "mape_summary": r.mape_summary_json,
            "model_versions": r.model_versions_json,
            "mom": mom,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        })
    return out
