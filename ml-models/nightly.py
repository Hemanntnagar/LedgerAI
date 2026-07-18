"""
Step 9 — Nightly automation: ML refresh for active subscriptions + health checks.

Callable from CLI (`python cli.py nightly-refresh`) or APScheduler.
Respects each subscription's `ml_refresh_hours` unless force=True.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("ml.nightly")


def _ensure_pipeline_path() -> str:
    pipeline = os.environ.get(
        "INVOICE_PIPELINE_ROOT",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "invoice-pipeline",
        ),
    )
    if pipeline not in sys.path:
        sys.path.insert(0, pipeline)
    return pipeline


def _ensure_insight_path() -> str:
    ie = os.environ.get(
        "INSIGHT_ENGINE_ROOT",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "insight-engine",
        ),
    )
    if ie not in sys.path:
        sys.path.insert(0, ie)
    return ie


def _ensure_ml_path():
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)


def stores_due_for_refresh(session, *, now: Optional[datetime] = None, force: bool = False):
    """Active subscriptions whose ml_refresh_hours have elapsed since last success."""
    from db.jobs import last_successful_job
    from db.models import Subscription

    now = now or datetime.utcnow()
    due = []
    for sub in session.query(Subscription).filter_by(status="active").all():
        if force:
            due.append(sub)
            continue
        hours = int(sub.ml_refresh_hours or 168)
        last = last_successful_job(session, "ml_refresh", store_id=sub.store_id)
        if last is None or last.finished_at is None:
            due.append(sub)
            continue
        if now - last.finished_at >= timedelta(hours=hours):
            due.append(sub)
    return due


def refresh_store(
    store_id: str,
    *,
    triggered_by: str = "cli",
    skip_forecast: bool = False,
    skip_insights: Optional[bool] = None,
) -> dict[str, Any]:
    """Run ML (+ optional insight engine) for one store; log a job_run."""
    _ensure_pipeline_path()
    _ensure_ml_path()

    from db.session import get_session, init_db
    from db.jobs import start_job, finish_job
    from orchestrator import run_all_modules

    init_db()
    session = get_session()
    job = start_job(
        session,
        "ml_refresh",
        store_id=store_id,
        triggered_by=triggered_by,
    )
    session.commit()

    if skip_insights is None:
        skip_insights = os.environ.get("SKIP_INSIGHTS_ON_NIGHTLY", "").lower() in (
            "1",
            "true",
            "yes",
        )

    try:
        ml_summary = run_all_modules(store_id=store_id, skip_forecast=skip_forecast)
        insight_summary = None
        if not skip_insights:
            try:
                _ensure_insight_path()
                from engine import run_insight_engine

                insight_summary = run_insight_engine(
                    store_id=store_id,
                    persist=True,
                    session=session,
                )
            except Exception as exc:
                logger.exception("insight engine failed for %s", store_id)
                insight_summary = {"error": str(exc)}

        # MAPE / confidence check after refresh
        from db.monitoring import check_mape_drift

        mape = check_mape_drift(session, store_id, raise_alerts=True)

        detail = {
            "ml": ml_summary,
            "insights": insight_summary,
            "mape_check": {
                "healthy": mape.get("healthy"),
                "fired": mape.get("fired"),
                "avg_mape_pct": (mape.get("current") or {}).get("avg_mape_pct"),
            },
        }
        ml_errors = [
            k for k, v in ml_summary.items() if isinstance(v, dict) and v.get("error")
        ]
        status = "failed" if ml_errors and len(ml_errors) == len(
            [k for k in ml_summary if k != "store_id"]
        ) else "success"
        # Partial module failure still counts as success for refresh cadence
        if ml_errors and status == "success":
            detail["module_errors"] = ml_errors

        finish_job(session, job, status=status, detail=detail)
        session.commit()
        return {"job_id": job.job_id, "store_id": store_id, "status": status, "detail": detail}
    except Exception as exc:
        session.rollback()
        # Re-attach job in a fresh transaction
        session = get_session()
        from db.models import JobRun

        job = session.query(JobRun).filter_by(job_id=job.job_id).first()
        if job:
            finish_job(session, job, status="failed", error_message=str(exc))
            session.commit()
        logger.exception("ml_refresh failed for %s", store_id)
        return {
            "job_id": job.job_id if job else None,
            "store_id": store_id,
            "status": "failed",
            "error": str(exc),
        }
    finally:
        session.close()


def run_nightly_refresh(
    *,
    force: bool = False,
    triggered_by: str = "scheduler",
    skip_forecast: bool = False,
    run_health: bool = True,
) -> dict[str, Any]:
    """
    Refresh every active subscription that is due, then run platform health checks.
    """
    _ensure_pipeline_path()
    _ensure_ml_path()

    from db.session import get_session, init_db
    from db.jobs import start_job, finish_job
    from db.monitoring import check_ingestion_health

    init_db()
    session = get_session()
    batch = start_job(
        session,
        "nightly_batch",
        store_id=None,
        triggered_by=triggered_by,
        detail={"force": force},
    )
    session.commit()

    try:
        due = stores_due_for_refresh(session, force=force)
        store_ids = [s.store_id for s in due]
        session.close()

        results = []
        for sid in store_ids:
            results.append(
                refresh_store(
                    sid,
                    triggered_by=triggered_by,
                    skip_forecast=skip_forecast,
                )
            )

        health = None
        if run_health:
            session = get_session()
            try:
                # Always record ingestion health; MAPE already checked per store
                health = {
                    "ingestion": check_ingestion_health(session, raise_alerts=True),
                }
                # Also catch any active stores not refreshed this run
                from db.models import Subscription

                all_active = [
                    s.store_id
                    for s in session.query(Subscription).filter_by(status="active").all()
                ]
                skipped = [s for s in all_active if s not in store_ids]
                if skipped:
                    from db.monitoring import check_mape_drift

                    health["mape_skipped_stores"] = [
                        check_mape_drift(session, sid, raise_alerts=True)
                        for sid in skipped
                    ]
                session.commit()
            finally:
                session.close()

        session = get_session()
        from db.models import JobRun

        batch = session.query(JobRun).filter_by(job_id=batch.job_id).first()
        summary = {
            "n_due": len(store_ids),
            "store_ids": store_ids,
            "results": [
                {"store_id": r["store_id"], "status": r["status"], "job_id": r.get("job_id")}
                for r in results
            ],
            "health": {
                "ingestion_healthy": (health or {}).get("ingestion", {}).get("healthy"),
            }
            if health
            else None,
        }
        n_failed = sum(1 for r in results if r["status"] == "failed")
        finish_job(
            session,
            batch,
            status="failed" if n_failed and n_failed == len(results) and results else "success",
            detail=summary,
        )
        session.commit()
        session.close()
        return {"batch_job_id": batch.job_id, **summary, "health": health}
    except Exception as exc:
        logger.exception("nightly batch failed")
        try:
            session = get_session()
            from db.models import JobRun

            batch = session.query(JobRun).filter_by(job_id=batch.job_id).first()
            if batch:
                finish_job(session, batch, status="failed", error_message=str(exc))
                session.commit()
            session.close()
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)}


def run_health_check_job(*, triggered_by: str = "cli") -> dict[str, Any]:
    """Standalone health-check job (ingestion + MAPE for all active stores)."""
    _ensure_pipeline_path()
    from db.session import get_session, init_db
    from db.jobs import start_job, finish_job
    from db.monitoring import run_health_checks

    init_db()
    session = get_session()
    job = start_job(session, "health_check", triggered_by=triggered_by)
    session.commit()
    try:
        result = run_health_checks(session, raise_alerts=True)
        finish_job(
            session,
            job,
            status="success",
            detail={
                "healthy": result.get("healthy"),
                "open_alert_count": result.get("open_alert_count"),
                "ingestion": result.get("ingestion"),
                "mape_fired": [
                    {"store_id": r["store_id"], "fired": r.get("fired")}
                    for r in result.get("mape", [])
                    if r.get("fired")
                ],
            },
        )
        session.commit()
        return {"job_id": job.job_id, **result}
    except Exception as exc:
        finish_job(session, job, status="failed", error_message=str(exc))
        session.commit()
        raise
    finally:
        session.close()
