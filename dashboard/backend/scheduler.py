"""
Step 9 — Optional in-process scheduler (APScheduler).

Enable with ENABLE_SCHEDULER=1 on the dashboard API process.
Cron defaults come from invoice-pipeline/config/monitoring.yaml.
For production, prefer system cron calling `ml-models/cli.py nightly-refresh`.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("dashboard.scheduler")

_scheduler = None


def _monitoring_cron() -> tuple[int, int]:
    hour, minute = 2, 0
    try:
        pipeline = os.environ.get(
            "INVOICE_PIPELINE_ROOT",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "invoice-pipeline",
            ),
        )
        if pipeline not in sys.path:
            sys.path.insert(0, pipeline)
        from db.monitoring import load_monitoring_config

        cfg = load_monitoring_config().get("scheduler", {})
        hour = int(cfg.get("cron_hour", 2))
        minute = int(cfg.get("cron_minute", 0))
    except Exception:
        logger.exception("Could not load monitoring scheduler config; using 02:00")
    return hour, minute


def _run_nightly():
    ml_root = os.environ.get(
        "ML_MODELS_ROOT",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ml-models",
        ),
    )
    if ml_root not in sys.path:
        sys.path.insert(0, ml_root)
    from nightly import run_nightly_refresh

    skip_forecast = os.environ.get("ML_SKIP_FORECAST_ON_NIGHTLY", "").lower() in (
        "1",
        "true",
        "yes",
    )
    logger.info("Nightly refresh starting (skip_forecast=%s)", skip_forecast)
    result = run_nightly_refresh(
        triggered_by="scheduler",
        skip_forecast=skip_forecast,
        run_health=True,
    )
    logger.info(
        "Nightly refresh done: n_due=%s",
        result.get("n_due"),
    )
    return result


def start_scheduler():
    """Start BackgroundScheduler if ENABLE_SCHEDULER is set. Idempotent."""
    global _scheduler
    if os.environ.get("ENABLE_SCHEDULER", "").lower() not in ("1", "true", "yes"):
        logger.info("Scheduler disabled (set ENABLE_SCHEDULER=1 to enable)")
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error(
            "ENABLE_SCHEDULER=1 but APScheduler is not installed. "
            "pip install APScheduler or use cron + `python cli.py nightly-refresh`."
        )
        return None

    hour, minute = _monitoring_cron()
    tz = "Asia/Kolkata"
    try:
        from db.monitoring import load_monitoring_config

        tz = load_monitoring_config().get("scheduler", {}).get("timezone", tz)
    except Exception:
        pass

    sched = BackgroundScheduler(timezone=tz)
    sched.add_job(
        _run_nightly,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="nightly_ml_refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    logger.info("Scheduler started: nightly ML refresh at %02d:%02d (%s)", hour, minute, tz)
    return sched


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
