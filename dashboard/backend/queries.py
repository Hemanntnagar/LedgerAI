"""
All aggregation queries live here, one function per metric group. Every
function does its grouping/summing in SQL (not in Python/pandas) for
performance, and accepts an already-open SQLAlchemy session plus filter
params so it's easily unit-testable.
"""
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import func, extract, case
from sqlalchemy.orm import Session

from dbconn import Store, Product, Invoice, InvoiceLineItem


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


def get_sales_trend(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None, granularity: str = "day") -> list:
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

    # simple trailing moving average (window=7) computed in Python since
    # it's a light post-processing step over an already-small result set
    window = 7
    for i, point in enumerate(series):
        lo = max(0, i - window + 1)
        window_vals = [p["revenue"] for p in series[lo:i + 1]]
        point["moving_avg_7"] = round(sum(window_vals) / len(window_vals), 2)

    return series


def get_top_products(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None, limit: int = 10, by: str = "revenue") -> list:
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


def get_category_breakdown(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None) -> list:
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


def get_time_heatmap(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None) -> list:
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
            {"day_of_week": int(r.day_of_week), "hour": int(r.hour), "revenue": round(float(r.revenue or 0), 2), "transactions": r.transactions}
            for r in rows
        ]
    else:
        # degrade gracefully: day-of-week only, no hour breakdown
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
            {"day_of_week": int(r.day_of_week), "hour": None, "revenue": round(float(r.revenue or 0), 2), "transactions": r.transactions}
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


def get_payment_methods(session: Session, start_date: date, end_date: date, store_id: Optional[str] = None) -> list:
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
