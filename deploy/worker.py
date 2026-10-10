"""
Background worker for analytics_refresh jobs (Render Worker / local).

Polls job_runs with status=queued, claims one at a time, runs:
  rollup revenue → ml-models refresh_store (ML + insights).

Usage:
  export INVOICE_DB_URL=postgresql+psycopg2://…
  export INVOICE_PIPELINE_ROOT=… ML_MODELS_ROOT=… INSIGHT_ENGINE_ROOT=…
  python deploy/worker.py
"""
from __future__ import annotations

import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ledger.worker")

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "5"))
JOB_TYPES = ["analytics_refresh", "ml_refresh"]


def _bootstrap_paths():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pipeline = os.environ.setdefault("INVOICE_PIPELINE_ROOT", os.path.join(root, "invoice-pipeline"))
    ml = os.environ.setdefault("ML_MODELS_ROOT", os.path.join(root, "ml-models"))
    ie = os.environ.setdefault("INSIGHT_ENGINE_ROOT", os.path.join(root, "insight-engine"))
    for p in (pipeline, ml, ie):
        if p not in sys.path:
            sys.path.insert(0, p)


def process_job(job) -> dict:
    from db.revenue_facts import rollup_revenue_facts
    from db.session import get_session
    from db.jobs import finish_job
    from nightly import refresh_store

    detail = dict(job.detail_json or {})
    skip_forecast = bool(detail.get("skip_forecast", True))
    include_rollup = detail.get("include_rollup", True)
    store_id = job.store_id
    if not store_id:
        raise ValueError("analytics job missing store_id")

    rollup_result = None
    if include_rollup:
        session = get_session()
        try:
            rollup_result = rollup_revenue_facts(session, store_id=store_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    result = refresh_store(
        store_id,
        triggered_by="worker",
        skip_forecast=skip_forecast,
    )
    if rollup_result is not None:
        result["rollup"] = rollup_result

    # Mark the queued job finished (refresh_store writes its own ml_refresh job)
    session = get_session()
    try:
        from db.models import JobRun

        row = session.query(JobRun).filter_by(job_id=job.job_id).first()
        if row:
            status = "success" if result.get("status") != "failed" else "failed"
            finish_job(
                session,
                row,
                status=status,
                detail={"worker_result": result},
                error_message=result.get("error"),
            )
            session.commit()
    finally:
        session.close()
    return result


def run_once() -> bool:
    """Claim and process one job. Returns True if work was done."""
    from db.session import get_session, init_db
    from db.jobs import claim_next_queued_job

    init_db()
    session = get_session()
    try:
        job = claim_next_queued_job(session, job_types=JOB_TYPES)
        if not job:
            session.commit()
            return False
        job_id = job.job_id
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("Processing job %s store=%s", job_id, getattr(job, "store_id", None))
    try:
        # Re-load job in fresh context via id
        from db.session import get_session as gs
        from db.models import JobRun

        s2 = gs()
        try:
            job = s2.query(JobRun).filter_by(job_id=job_id).first()
        finally:
            s2.close()
        process_job(job)
        logger.info("Job %s done", job_id)
    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        from db.session import get_session as gs
        from db.models import JobRun
        from db.jobs import finish_job

        s3 = gs()
        try:
            row = s3.query(JobRun).filter_by(job_id=job_id).first()
            if row:
                finish_job(s3, row, status="failed", error_message=str(exc))
                s3.commit()
        finally:
            s3.close()
    return True


def main():
    _bootstrap_paths()
    logger.info("Ledger worker started (poll=%ss)", POLL_SECONDS)
    while True:
        try:
            did = run_once()
            if not did:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            break
        except Exception:
            logger.exception("Worker loop error")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
