"""
Step 10 — Pilot API: outcomes, historical correlation, threshold tune.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, List, Optional

from dbconn import get_session
from tenant_auth import require_store_auth

router = APIRouter(prefix="/api/v1/pilot", tags=["pilot"])


class TuneBody(BaseModel):
    rule: str = Field(..., description="Rule name under rules.yaml, e.g. bundle")
    param: str = Field(..., description="Parameter name, e.g. min_lift")
    values: List[Any] = Field(..., description="Grid of candidate values")


@router.get("/summary")
def pilot_summary_endpoint(
    auth: dict = Depends(require_store_auth),
    baseline_days: Optional[int] = Query(None, ge=7, le=90),
    followup_days: Optional[int] = Query(None, ge=7, le=90),
):
    """Accepted-action before/after + historical month correlation."""
    from db.pilot import pilot_summary

    session = get_session()
    try:
        return pilot_summary(
            session,
            auth["store_id"],
            baseline_days=baseline_days,
            followup_days=followup_days,
        )
    finally:
        session.close()


@router.get("/outcomes")
def pilot_outcomes_endpoint(
    auth: dict = Depends(require_store_auth),
    baseline_days: Optional[int] = Query(None, ge=7, le=90),
    followup_days: Optional[int] = Query(None, ge=7, le=90),
    limit: int = Query(50, ge=1, le=200),
):
    """Per accepted/done insight: baseline vs follow-up revenue."""
    from db.pilot import accepted_action_outcomes, summarize_outcomes

    session = get_session()
    try:
        rows = accepted_action_outcomes(
            session,
            auth["store_id"],
            baseline_days=baseline_days,
            followup_days=followup_days,
            limit=limit,
        )
        return {
            "store_id": auth["store_id"],
            "summary": summarize_outcomes(rows),
            "data": rows,
        }
    finally:
        session.close()


@router.post("/backtest")
def pilot_backtest_endpoint(auth: dict = Depends(require_store_auth)):
    """
    Run historical correlation backtest and log job_runs (pilot_backtest).
    """
    import os
    import sys

    insight_root = os.environ.get("INSIGHT_ENGINE_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "insight-engine",
    )
    # Prefer insight-engine modules; keep pipeline on path for db.*
    if insight_root in sys.path:
        sys.path.remove(insight_root)
    sys.path.insert(0, insight_root)

    from backtest import backtest_historical_months

    try:
        return backtest_historical_months(
            store_id=auth["store_id"],
            triggered_by="api",
            log_job=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tune")
def pilot_tune_endpoint(
    body: TuneBody,
    auth: dict = Depends(require_store_auth),
):
    """Simulate a threshold grid for one rule (dry-run, no persist)."""
    import os
    import sys

    insight_root = os.environ.get("INSIGHT_ENGINE_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "insight-engine",
    )
    if insight_root in sys.path:
        sys.path.remove(insight_root)
    sys.path.insert(0, insight_root)

    if not body.values:
        raise HTTPException(status_code=400, detail="values must be a non-empty list")

    from tuning import simulate_rule_thresholds

    try:
        return simulate_rule_thresholds(
            store_id=auth["store_id"],
            rule_name=body.rule,
            param=body.param,
            values=list(body.values),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
