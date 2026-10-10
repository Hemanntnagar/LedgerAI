"""
Step 9 — Monitoring API: job runs, health, alerts (store-scoped).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from dbconn import get_session
from tenant_auth import require_store_auth

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


class AckBody(BaseModel):
    status: str = "acknowledged"  # acknowledged | resolved


@router.get("/health")
def monitoring_health(auth: dict = Depends(require_store_auth)):
    """Ingestion (platform) + MAPE/confidence status for the signed-in store."""
    from db.monitoring import check_ingestion_health, check_mape_drift, list_alerts, alert_to_dict

    session = get_session()
    try:
        ingestion = check_ingestion_health(session, raise_alerts=False)
        mape = check_mape_drift(session, auth["store_id"], raise_alerts=False)
        open_alerts = list_alerts(session, store_id=auth["store_id"], status="open", limit=20)
        return {
            "store_id": auth["store_id"],
            "healthy": bool(ingestion.get("healthy") and mape.get("healthy")),
            "ingestion": ingestion,
            "mape": mape,
            "open_alerts": [alert_to_dict(a) for a in open_alerts],
        }
    finally:
        session.close()


@router.get("/jobs")
def monitoring_jobs(
    auth: dict = Depends(require_store_auth),
    job_type: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    """Recent job_runs for this store (plus platform-wide nightly_batch rows)."""
    from db.jobs import list_job_runs, job_to_dict
    from db.models import JobRun

    session = get_session()
    try:
        store_jobs = list_job_runs(
            session, store_id=auth["store_id"], job_type=job_type, limit=limit
        )
        # Include recent platform batch jobs (no store_id)
        q = session.query(JobRun).filter(JobRun.store_id.is_(None))
        if job_type:
            q = q.filter(JobRun.job_type == job_type)
        platform = q.order_by(JobRun.started_at.desc()).limit(min(10, limit)).all()
        # Merge by started_at
        merged = sorted(
            list(store_jobs) + list(platform),
            key=lambda j: j.started_at or j.finished_at,
            reverse=True,
        )[:limit]
        return {"data": [job_to_dict(j) for j in merged]}
    finally:
        session.close()


@router.get("/alerts")
def monitoring_alerts(
    auth: dict = Depends(require_store_auth),
    status: str = Query("open"),
    limit: int = Query(50, ge=1, le=200),
):
    from db.monitoring import list_alerts, alert_to_dict

    session = get_session()
    try:
        rows = list_alerts(
            session, store_id=auth["store_id"], status=status or None, limit=limit
        )
        return {"data": [alert_to_dict(a) for a in rows]}
    finally:
        session.close()


@router.post("/alerts/{alert_id}/ack")
def ack_alert(
    alert_id: str,
    body: AckBody = AckBody(),
    auth: dict = Depends(require_store_auth),
):
    from datetime import datetime
    from db.models import MonitoringAlert
    from db.monitoring import alert_to_dict

    session = get_session()
    try:
        row = session.query(MonitoringAlert).filter_by(alert_id=alert_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        if row.store_id and row.store_id != auth["store_id"]:
            raise HTTPException(status_code=403, detail="Alert belongs to another store")
        if body.status not in ("acknowledged", "resolved"):
            raise HTTPException(status_code=400, detail="status must be acknowledged or resolved")
        row.status = body.status
        if body.status == "acknowledged":
            row.acknowledged_at = datetime.utcnow()
        else:
            row.resolved_at = datetime.utcnow()
            if not row.acknowledged_at:
                row.acknowledged_at = row.resolved_at
        session.commit()
        return alert_to_dict(row)
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()


@router.post("/refresh")
def trigger_refresh(
    auth: dict = Depends(require_store_auth),
    skip_forecast: bool = Query(False),
    include_rollup: bool = Query(True, description="Roll up revenue facts before ML"),
):
    """
    Manually trigger analytics for the signed-in store:
    revenue rollup → ML modules → insight engine (same path as nightly).

    When ANALYTICS_ASYNC=1, enqueues a job for the background worker instead.
    """
    import os
    import sys

    store_id = auth["store_id"]
    async_mode = os.environ.get("ANALYTICS_ASYNC", "").lower() in ("1", "true", "yes")

    if async_mode:
        from db.jobs import enqueue_job, job_to_dict

        session = get_session()
        try:
            job = enqueue_job(
                session,
                "analytics_refresh",
                store_id=store_id,
                triggered_by="api",
                detail={"skip_forecast": skip_forecast, "include_rollup": include_rollup},
            )
            session.commit()
            return {
                "job_id": job.job_id,
                "store_id": store_id,
                "status": "queued",
                "detail": job_to_dict(job),
            }
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            session.close()

    rollup_result = None
    if include_rollup:
        from db.revenue_facts import rollup_revenue_facts

        session = get_session()
        try:
            rollup_result = rollup_revenue_facts(session, store_id=store_id)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"rollup failed: {exc}") from exc
        finally:
            session.close()

    ml_root = os.environ.get(
        "ML_MODELS_ROOT",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ml-models",
        ),
    )
    if ml_root not in sys.path:
        sys.path.insert(0, ml_root)
    from nightly import refresh_store

    result = refresh_store(
        store_id,
        triggered_by="api",
        skip_forecast=skip_forecast,
    )
    if rollup_result is not None:
        result["rollup"] = rollup_result
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("error") or "refresh failed")
    return result
