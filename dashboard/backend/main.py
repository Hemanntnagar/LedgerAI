"""
FastAPI backend for the Phase 2 analytics dashboard.

All endpoints share the same query params: start_date, end_date,
store_id (optional, "all" if omitted). When a store session token is
present (Step 2), store_id is forced to that store — tenants cannot
read each other's data. Unauthenticated access remains available for
local demo / admin use.
"""
import os
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dbconn import get_session, Store
import queries
from cache import cache
from live_ingest_api import router as live_ingest_router
from auth_api import router as auth_router
from subscription_api import router as subscription_router
from loyalty_api import router as loyalty_router
from ml_api import router as ml_router
from admin_api import router as admin_router
from monitoring_api import router as monitoring_router
from pilot_api import router as pilot_router
from tenant_auth import scope_store_id

app = FastAPI(title="Invoice Analytics Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(live_ingest_router)
app.include_router(auth_router)
app.include_router(subscription_router)
app.include_router(loyalty_router)
app.include_router(ml_router)
app.include_router(admin_router)
app.include_router(monitoring_router)
app.include_router(pilot_router)

# Upload needs python-multipart; keep auth/API up if the package is missing.
try:
    from upload_api import router as upload_router

    app.include_router(upload_router)
except Exception:
    import logging

    logging.getLogger("dashboard").exception(
        "Upload API disabled (install python-multipart for file uploads)"
    )

_FRONTEND_DIR = os.environ.get(
    "LEDGER_FRONTEND_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
    ),
)
_FRONTEND_INDEX = os.path.join(_FRONTEND_DIR, "index.html")


@app.get("/")
def serve_frontend_index():
    """Serve the store UI from the same origin as the API (local / Render)."""
    if os.path.isfile(_FRONTEND_INDEX):
        return FileResponse(_FRONTEND_INDEX, media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend not found")


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="frontend-static")


@app.on_event("startup")
def _startup_scheduler():
    try:
        from scheduler import start_scheduler
        start_scheduler()
    except Exception:
        # Never block API boot on scheduler failure
        import logging
        logging.getLogger("dashboard").exception("Failed to start scheduler")


@app.on_event("shutdown")
def _shutdown_scheduler():
    try:
        from scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


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


def _resolve_scope(
    store_id: Optional[str],
    authorization: Optional[str],
    x_store_token: Optional[str],
) -> Optional[str]:
    scoped, _auth = scope_store_id(store_id, authorization, x_store_token)
    return scoped


@app.get("/api/kpis")
def kpis(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
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
def sales_trend(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    granularity: str = Query("day"),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
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


@app.get("/api/revenue")
def revenue(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    granularity: str = Query("day", description="day | week | month"),
    prefer_facts: bool = Query(True, description="Use rolled-up facts when available"),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    """
    Step 3 revenue foundation: day / week / month series with totals.
    Prefers daily_revenue_facts / monthly_revenue_facts after rollup-revenue.
    """
    store_id = _resolve_scope(store_id, authorization, x_store_token)
    _validate_dates(start_date, end_date)
    _validate_store(store_id)
    if granularity not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="granularity must be day, week, or month")
    cache_key = f"revenue:{start_date}:{end_date}:{store_id}:{granularity}:{prefer_facts}"

    def compute():
        session = get_session()
        try:
            return queries.get_revenue_by_period(
                session, start_date, end_date, store_id, granularity, prefer_facts
            )
        finally:
            session.close()

    data = cache.get_or_set(cache_key, CACHE_TTL_SECONDS, compute)
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/top-products")
def top_products(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    limit: int = 10,
    by: str = Query("revenue"),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
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
def pareto(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_pareto(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/category-breakdown")
def category_breakdown(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_category_breakdown(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/time-heatmap")
def time_heatmap(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_time_heatmap(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/store-comparison")
def store_comparison(
    start_date: date,
    end_date: date,
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    _validate_dates(start_date, end_date)
    scoped, _auth = scope_store_id(None, authorization, x_store_token)

    session = get_session()
    try:
        data = queries.get_store_comparison(session, start_date, end_date)
        if scoped:
            data = [row for row in data if row["store_id"] == scoped]
    finally:
        session.close()
    return _envelope(data, start_date, end_date, scoped)


@app.get("/api/payment-methods")
def payment_methods(
    start_date: date,
    end_date: date,
    store_id: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    store_id = _resolve_scope(store_id, authorization, x_store_token)
    _validate_dates(start_date, end_date)
    _validate_store(store_id)

    session = get_session()
    try:
        data = queries.get_payment_methods(session, start_date, end_date, store_id)
    finally:
        session.close()
    return _envelope(data, start_date, end_date, store_id)


@app.get("/api/stores")
def list_stores(
    authorization: Optional[str] = Header(None),
    x_store_token: Optional[str] = Header(None, alias="X-Store-Token"),
):
    """Helper endpoint for the frontend's store selector dropdown."""
    scoped, _auth = scope_store_id(None, authorization, x_store_token)
    session = get_session()
    try:
        q = session.query(Store)
        if scoped:
            q = q.filter(Store.store_id == scoped)
        stores = q.all()
        return {"data": [{"store_id": s.store_id, "store_name": s.store_name} for s in stores]}
    finally:
        session.close()


@app.get("/health")
def health():
    return {"status": "ok"}
