"""
Step 3 — roll up invoice rows into daily / monthly revenue facts.

Daily facts are the grain for week views; monthly facts freeze month-end
totals (plus category/product breakdowns) so history stays stable.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import (
    DailyRevenueFact,
    Invoice,
    InvoiceLineItem,
    MonthlyRevenueFact,
    Product,
)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _units_for_day(session: Session, store_id: str, day: date) -> float:
    val = (
        session.query(func.coalesce(func.sum(InvoiceLineItem.quantity), 0))
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(Invoice.store_id == store_id, Invoice.invoice_date == day)
        .scalar()
    )
    return float(val or 0)


def upsert_daily_facts(
    session: Session,
    *,
    store_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> int:
    """
    Recompute daily_revenue_facts from invoices for the date range.
    Returns number of day×store rows upserted.
    """
    q = session.query(
        Invoice.store_id,
        Invoice.invoice_date,
        func.coalesce(func.sum(Invoice.grand_total), 0).label("revenue"),
        func.count(func.distinct(Invoice.invoice_id)).label("transactions"),
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    if start_date:
        q = q.filter(Invoice.invoice_date >= start_date)
    if end_date:
        q = q.filter(Invoice.invoice_date <= end_date)
    q = q.group_by(Invoice.store_id, Invoice.invoice_date)
    rows = q.all()

    now = datetime.utcnow()
    count = 0
    for r in rows:
        units = _units_for_day(session, r.store_id, r.invoice_date)
        revenue = float(r.revenue or 0)
        txns = int(r.transactions or 0)
        avg_basket = (revenue / txns) if txns else 0.0

        existing = (
            session.query(DailyRevenueFact)
            .filter_by(store_id=r.store_id, fact_date=r.invoice_date)
            .first()
        )
        if existing:
            existing.revenue = revenue
            existing.transactions = txns
            existing.units_sold = units
            existing.avg_basket_value = round(avg_basket, 2)
            existing.computed_at = now
        else:
            session.add(
                DailyRevenueFact(
                    store_id=r.store_id,
                    fact_date=r.invoice_date,
                    revenue=revenue,
                    transactions=txns,
                    units_sold=units,
                    avg_basket_value=round(avg_basket, 2),
                    computed_at=now,
                )
            )
        count += 1
    return count


def _category_breakdown(session: Session, store_id: str, start: date, end: date) -> list:
    rows = (
        session.query(
            func.coalesce(Product.category, "Uncategorized").label("category"),
            func.sum(InvoiceLineItem.line_total).label("revenue"),
            func.sum(InvoiceLineItem.quantity).label("units"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.product_id == Product.product_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(
            Invoice.store_id == store_id,
            Invoice.invoice_date.between(start, end),
        )
        .group_by("category")
        .all()
    )
    return [
        {
            "category": r.category,
            "revenue": round(float(r.revenue or 0), 2),
            "units": float(r.units or 0),
        }
        for r in rows
    ]


def _product_breakdown(
    session: Session, store_id: str, start: date, end: date, limit: int = 50
) -> list:
    rows = (
        session.query(
            Product.product_id,
            Product.product_name,
            func.sum(InvoiceLineItem.line_total).label("revenue"),
            func.sum(InvoiceLineItem.quantity).label("units"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.product_id == Product.product_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(
            Invoice.store_id == store_id,
            Invoice.invoice_date.between(start, end),
        )
        .group_by(Product.product_id, Product.product_name)
        .all()
    )
    results = [
        {
            "product_id": r.product_id,
            "product_name": r.product_name,
            "revenue": round(float(r.revenue or 0), 2),
            "units": float(r.units or 0),
        }
        for r in rows
    ]
    results.sort(key=lambda x: x["revenue"], reverse=True)
    return results[:limit]


def upsert_monthly_facts(
    session: Session,
    *,
    store_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> int:
    """
    Recompute monthly_revenue_facts from invoices (not from daily facts,
    so numbers stay consistent with live SQL).
    Returns number of month×store rows upserted.
    """
    # Discover (store, year, month) buckets with invoices in range
    year_col = func.strftime("%Y", Invoice.invoice_date)
    month_col = func.strftime("%m", Invoice.invoice_date)

    q = session.query(
        Invoice.store_id,
        year_col.label("y"),
        month_col.label("m"),
        func.coalesce(func.sum(Invoice.grand_total), 0).label("revenue"),
        func.count(func.distinct(Invoice.invoice_id)).label("transactions"),
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    if start_date:
        q = q.filter(Invoice.invoice_date >= start_date)
    if end_date:
        q = q.filter(Invoice.invoice_date <= end_date)
    q = q.group_by(Invoice.store_id, "y", "m")
    buckets = q.all()

    now = datetime.utcnow()
    today = date.today()
    count = 0
    for b in buckets:
        year = int(b.y)
        month = int(b.m)
        period_start, period_end = _month_bounds(year, month)

        units = (
            session.query(func.coalesce(func.sum(InvoiceLineItem.quantity), 0))
            .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
            .filter(
                Invoice.store_id == b.store_id,
                Invoice.invoice_date.between(period_start, period_end),
            )
            .scalar()
        )
        units = float(units or 0)
        revenue = float(b.revenue or 0)
        txns = int(b.transactions or 0)
        avg_basket = (revenue / txns) if txns else 0.0
        by_cat = _category_breakdown(session, b.store_id, period_start, period_end)
        by_prod = _product_breakdown(session, b.store_id, period_start, period_end)
        frozen = period_end < today

        existing = (
            session.query(MonthlyRevenueFact)
            .filter_by(store_id=b.store_id, year=year, month=month)
            .first()
        )
        if existing:
            existing.period_start = period_start
            existing.period_end = period_end
            existing.revenue = revenue
            existing.transactions = txns
            existing.units_sold = units
            existing.avg_basket_value = round(avg_basket, 2)
            existing.by_category_json = by_cat
            existing.by_product_json = by_prod
            existing.computed_at = now
            existing.is_frozen = frozen
        else:
            session.add(
                MonthlyRevenueFact(
                    store_id=b.store_id,
                    year=year,
                    month=month,
                    period_start=period_start,
                    period_end=period_end,
                    revenue=revenue,
                    transactions=txns,
                    units_sold=units,
                    avg_basket_value=round(avg_basket, 2),
                    by_category_json=by_cat,
                    by_product_json=by_prod,
                    computed_at=now,
                    is_frozen=frozen,
                )
            )
        count += 1
    return count


def rollup_revenue_facts(
    session: Session,
    *,
    store_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    """Run daily then monthly rollups. Caller commits."""
    daily_n = upsert_daily_facts(
        session, store_id=store_id, start_date=start_date, end_date=end_date
    )
    monthly_n = upsert_monthly_facts(
        session, store_id=store_id, start_date=start_date, end_date=end_date
    )
    return {"daily_rows": daily_n, "monthly_rows": monthly_n}


def week_start(d: date) -> date:
    """Monday-start ISO week."""
    return d - timedelta(days=d.weekday())


def aggregate_weekly_from_daily(
    session: Session,
    *,
    start_date: date,
    end_date: date,
    store_id: Optional[str] = None,
) -> list[dict]:
    """
    Build week buckets from daily_revenue_facts (falls back empty if not rolled up).
    Week key is ISO year-week: YYYY-Www.
    """
    q = session.query(DailyRevenueFact).filter(
        DailyRevenueFact.fact_date.between(start_date, end_date)
    )
    if store_id and store_id != "all":
        q = q.filter(DailyRevenueFact.store_id == store_id)
    rows = q.all()

    buckets: dict[str, dict] = {}
    for r in rows:
        ws = week_start(r.fact_date)
        iso = r.fact_date.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        if key not in buckets:
            buckets[key] = {
                "period": key,
                "period_start": ws.isoformat(),
                "revenue": 0.0,
                "transactions": 0,
                "units_sold": 0.0,
            }
        bucket = buckets[key]
        bucket["revenue"] += float(r.revenue or 0)
        bucket["transactions"] += int(r.transactions or 0)
        bucket["units_sold"] += float(r.units_sold or 0)

    results = []
    for period in sorted(buckets.keys()):
        b = buckets[period]
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
