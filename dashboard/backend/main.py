"""
FastAPI backend for the Phase 2 analytics dashboard.

All endpoints share the same query params: start_date, end_date,
store_id (optional, "all" if omitted). Results are wrapped in a
consistent envelope and cached briefly since dashboards get polled often.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from dbconn import get_session, Store
import queries
from cache import cache

app = FastAPI(title="Invoice Analytics Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_TTL_SECONDS = 300


def _envelope(data, start_date: date, end_date: date, store_id: Optional[str]):
    return {
        "data": data,
        "meta": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "store_id": store_id or "all",
            "generated_at": datetime.utcnow().isoformat() + "Z",
        },
    }


def _validate_dates(start_date: date, end_date: date):
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")


def _validate_store(store_id: Optional[str]):
    if store_id and store_id != "all":
        session = get_session()
        try:
            store = session.query(Store).filter_by(store_id=store_id).first()
            if not store:
                raise HTTPException(status_code=400, detail=f"Unknown store_id: {store_id}")
        finally:
            session.close()


@app.get("/api/kpis")
def kpis(start_date: date, end_date: date, store_id: Optional[str] = Query(None)):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)
    cache_key = f"kpis:{start_date}:{end_date}:{store_id}"

    def compute():
        session = get_session()
        try:
            return queries.get_kpis(session, start_date, end_date, store_id)
        finally:
            session.close()

    data = cache.get_or_set(cache_key, CACHE_TTL_SECONDS, compute)
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/sales-trend")
def sales_trend(start_date: date, end_date: date, store_id: Optional[str] = Query(None), granularity: str = Query("day")):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)
    if granularity not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="granularity must be day, week, or month")
    cache_key = f"sales_trend:{start_date}:{end_date}:{store_id}:{granularity}"

    def compute():
        session = get_session()
        try:
            return queries.get_sales_trend(session, start_date, end_date, store_id, granularity)
        finally:
            session.close()

    data = cache.get_or_set(cache_key, CACHE_TTL_SECONDS, compute)
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/top-products")
def top_products(start_date: date, end_date: date, store_id: Optional[str] = Query(None), limit: int = 10, by: str = Query("revenue")):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)
    if by not in ("revenue", "units"):
        raise HTTPException(status_code=400, detail="by must be 'revenue' or 'units'")

    session = get_session()
    try:
        data = queries.get_top_products(session, start_date, end_date, store_id, limit, by)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/pareto")
def pareto(start_date: date, end_date: date, store_id: Optional[str] = Query(None)):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_pareto(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/category-breakdown")
def category_breakdown(start_date: date, end_date: date, store_id: Optional[str] = Query(None)):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_category_breakdown(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/time-heatmap")
def time_heatmap(start_date: date, end_date: date, store_id: Optional[str] = Query(None)):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_time_heatmap(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/store-comparison")
def store_comparison(start_date: date, end_date: date):
    _validate_dates(start_date, end_date)

    session = get_session()
    try:
        data = queries.get_store_comparison(session, start_date, end_date)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, None)


@app.get("/api/payment-methods")
def payment_methods(start_date: date, end_date: date, store_id: Optional[str] = Query(None)):
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_payment_methods(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/stores")
def list_stores():
    """Helper endpoint for the frontend's store selector dropdown."""
    session = get_session()
    try:
        stores = session.query(Store).all()
        return {"data": [{"store_id": s.store_id, "store_name": s.store_name} for s in stores]}
    finally:
        session.close()


@app.get("/health")
def health():
    return {"status": "ok"}
