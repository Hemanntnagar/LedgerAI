"""
Step 9 — Job run logging helpers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from db.models import JobRun


def start_job(
    session,
    job_type: str,
    *,
    store_id: Optional[str] = None,
    triggered_by: str = "cli",
    detail: Optional[dict] = None,
    status: str = "running",
) -> JobRun:
    row = JobRun(
        job_type=job_type,
        store_id=store_id,
        status=status,
        started_at=datetime.utcnow() if status == "running" else None,
        detail_json=detail,
        triggered_by=triggered_by,
    )
    session.add(row)
    session.flush()
    return row


def enqueue_job(
    session,
    job_type: str,
    *,
    store_id: Optional[str] = None,
    triggered_by: str = "api",
    detail: Optional[dict] = None,
) -> JobRun:
    """Create a queued job for the background worker to claim."""
    return start_job(
        session,
        job_type,
        store_id=store_id,
        triggered_by=triggered_by,
        detail=detail,
        status="queued",
    )


def claim_next_queued_job(
    session,
    *,
    job_types: Optional[list[str]] = None,
) -> Optional[JobRun]:
    """
    Atomically claim the oldest queued job (SQLite / Postgres).
    Sets status=running and started_at.
    """
    q = session.query(JobRun).filter(JobRun.status == "queued")
    if job_types:
        q = q.filter(JobRun.job_type.in_(job_types))
    job = q.order_by(JobRun.job_id.asc()).first()
    if not job:
        return None
    job.status = "running"
    job.started_at = datetime.utcnow()
    session.flush()
    return job


def finish_job(
    session,
    job: JobRun,
    *,
    status: str,
    detail: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> JobRun:
    job.status = status
    job.finished_at = datetime.utcnow()
    if detail is not None:
        existing = dict(job.detail_json or {})
        existing.update(detail)
        job.detail_json = existing
    if error_message is not None:
        job.error_message = error_message
    session.flush()
    return job


def last_successful_job(
    session,
    job_type: str,
    *,
    store_id: Optional[str] = None,
) -> Optional[JobRun]:
    q = session.query(JobRun).filter_by(job_type=job_type, status="success")
    if store_id is not None:
        q = q.filter(JobRun.store_id == store_id)
    else:
        q = q.filter(JobRun.store_id.is_(None))
    return q.order_by(JobRun.finished_at.desc()).first()


def list_job_runs(
    session,
    *,
    store_id: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 50,
) -> list[JobRun]:
    q = session.query(JobRun)
    if store_id is not None:
        q = q.filter(JobRun.store_id == store_id)
    if job_type is not None:
        q = q.filter(JobRun.job_type == job_type)
    return q.order_by(JobRun.started_at.desc()).limit(limit).all()


def job_to_dict(job: JobRun) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "store_id": job.store_id,
        "status": job.status,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "detail": job.detail_json,
        "error_message": job.error_message,
        "triggered_by": job.triggered_by,
    }
