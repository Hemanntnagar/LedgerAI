"""
Step 10 — Historical backtest of insight rules vs next-month revenue lift.

Uses persisted insights (tagged with period_start/end) plus monthly revenue.
Does not re-train ML as-of a cutoff (collectors remain point-in-time); for a
true point-in-time replay, re-run ML with invoice filters then dry-run the engine.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

logger = logging.getLogger("insight_engine.backtest")


def backtest_historical_months(
    store_id: Optional[str] = None,
    store_name: Optional[str] = None,
    *,
    session=None,
    triggered_by: str = "cli",
    log_job: bool = True,
) -> dict[str, Any]:
    """
    Correlate months where actions were accepted with subsequent revenue lift.
    Logs a job_runs row (job_type=pilot_backtest) when log_job=True.
    """
    from db.session import get_session, init_db, engine
    from db.models import Base, Store
    from db.pilot import historical_action_correlation, pilot_summary
    from db.jobs import start_job, finish_job

    Base.metadata.create_all(bind=engine)

    own = session is None
    session = session or get_session()
    job = None
    try:
        if own:
            init_db()

        sid = store_id
        if not sid and store_name:
            store = session.query(Store).filter(Store.store_name == store_name).first()
            if not store:
                raise ValueError(f"Store not found: {store_name}")
            sid = store.store_id
        if not sid:
            raise ValueError("store_id or store_name is required")

        if log_job:
            job = start_job(
                session,
                "pilot_backtest",
                store_id=sid,
                triggered_by=triggered_by,
                detail={"phase": "start"},
            )
            session.commit()

        correlation = historical_action_correlation(session, sid)
        summary = pilot_summary(session, sid)

        result = {
            "store_id": sid,
            "correlation": correlation,
            "outcomes": summary["outcomes"],
            "feedback_tallies": summary["feedback_tallies"],
            "verdict": _verdict(correlation, summary["outcomes"]),
        }

        if job is not None:
            finish_job(
                session,
                job,
                status="success",
                detail={
                    "result": {
                        "lift_gap_pp": correlation.get("lift_gap_pp"),
                        "n_months": correlation.get("n_months_with_next"),
                        "avg_outcome_pct": summary["outcomes"].get(
                            "avg_revenue_pct_change"
                        ),
                    }
                },
            )
            session.commit()
            result["job_id"] = job.job_id

        return result
    except Exception as exc:
        if job is not None:
            finish_job(session, job, status="failed", error_message=str(exc))
            session.commit()
        raise
    finally:
        if own:
            session.close()


def _verdict(correlation: dict, outcomes: dict) -> dict[str, Any]:
    """Human-readable pilot signal — not a causal claim."""
    gap = correlation.get("lift_gap_pp")
    avg_outcome = outcomes.get("avg_revenue_pct_change")
    enough = correlation.get("enough_history")
    n_meas = outcomes.get("n_measurable", 0)

    signals = []
    if enough and gap is not None:
        if gap > 2:
            signals.append("acted months show higher next-month revenue on average")
        elif gap < -2:
            signals.append(
                "acted months show lower next-month revenue — review rule thresholds"
            )
        else:
            signals.append("little difference between acted and idle months yet")
    else:
        signals.append("need more month history for correlation")

    if n_meas >= 3 and avg_outcome is not None:
        if avg_outcome > 0:
            signals.append(
                f"accepted actions avg {avg_outcome}% revenue change in follow-up window"
            )
        else:
            signals.append(
                f"accepted actions avg {avg_outcome}% in follow-up — tune or dismiss weak rules"
            )
    elif n_meas == 0:
        signals.append(
            "accept actions in the Action Center to start measuring before/after"
        )

    return {
        "enough_history": bool(enough),
        "lift_gap_pp": gap,
        "avg_outcome_pct": avg_outcome,
        "signals": signals,
    }


def dry_run_for_period(
    store_id: str,
    period_start: date,
    period_end: date,
    *,
    config_path: Optional[str] = None,
    session=None,
) -> dict[str, Any]:
    """Score rules without persisting — useful before tagging a pilot month."""
    from engine import run_insight_engine

    return run_insight_engine(
        store_id=store_id,
        period_start=period_start,
        period_end=period_end,
        config_path=config_path,
        persist=False,
        session=session,
    )
