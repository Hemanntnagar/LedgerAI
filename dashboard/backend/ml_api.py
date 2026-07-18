"""
Step 8 — ML Intelligence read APIs for the store app.
Tenant-scoped; requires store session.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from dbconn import get_session
from tenant_auth import require_store_auth
import ml_queries

router = APIRouter(prefix="/api/v1/ml", tags=["ml"])


@router.get("/summary")
def ml_summary(auth: dict = Depends(require_store_auth)):
    """Row counts per ML module for the authenticated store."""
    session = get_session()
    try:
        try:
            counts = ml_queries.ml_module_counts(session, auth["store_id"])
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"data": counts, "meta": {"store_id": auth["store_id"]}}
    finally:
        session.close()


@router.get("/forecast")
def ml_forecast(
    auth: dict = Depends(require_store_auth),
    limit: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        data = ml_queries.get_forecast_rows(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"], "count": len(data)}}
    finally:
        session.close()


@router.get("/bundles")
def ml_bundles(
    auth: dict = Depends(require_store_auth),
    limit: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        data = ml_queries.get_bundle_rows(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"], "count": len(data)}}
    finally:
        session.close()


@router.get("/pricing")
def ml_pricing(
    auth: dict = Depends(require_store_auth),
    limit: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        data = ml_queries.get_pricing_rows(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"], "count": len(data)}}
    finally:
        session.close()


@router.get("/inventory")
def ml_inventory(
    auth: dict = Depends(require_store_auth),
    limit: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        data = ml_queries.get_inventory_rows(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"], "count": len(data)}}
    finally:
        session.close()


@router.get("/customers")
def ml_customers(
    auth: dict = Depends(require_store_auth),
    limit: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        data = ml_queries.get_customer_rows(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"], "count": len(data)}}
    finally:
        session.close()


@router.get("/festivals")
def ml_festivals(
    auth: dict = Depends(require_store_auth),
    limit: int = Query(50, ge=1, le=200),
):
    session = get_session()
    try:
        data = ml_queries.get_festival_rows(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"], "count": len(data)}}
    finally:
        session.close()
