"""
Subscription status + report generation with quota gate (Step 2).
Step 7: richer snapshots (MAPE + MoM), report list, insight feedback.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dbconn import get_session
import queries
from tenant_auth import require_store_auth

router = APIRouter(prefix="/api/v1", tags=["subscription"])


class GenerateReportRequest(BaseModel):
    period_start: date
    period_end: date
    period_type: str = Field(default="monthly")


class InsightFeedbackRequest(BaseModel):
    feedback: Optional[str] = Field(
        default=None,
        description="useful | not_useful | null to clear",
    )
    status: Optional[str] = Field(
        default=None,
        description="optional status update: new|accepted|dismissed|done",
    )


@router.get("/subscription")
def get_subscription(auth: dict = Depends(require_store_auth)):
    from db.models import Subscription, SubscriptionPlan
    from db.subscriptions import check_report_quota, ensure_billing_period

    session = get_session()
    try:
        sub = session.query(Subscription).filter_by(store_id=auth["store_id"]).first()
        if not sub:
            raise HTTPException(status_code=404, detail="No subscription for this store")
        ensure_billing_period(sub)
        session.commit()
        plan = session.query(SubscriptionPlan).filter_by(plan_code=sub.plan).first()
        quota = check_report_quota(sub)
        return {
            "store_id": auth["store_id"],
            "plan": sub.plan,
            "plan_display_name": plan.display_name if plan else sub.plan,
            "status": sub.status,
            "monthly_report_quota": sub.monthly_report_quota,
            "reports_used_this_period": sub.reports_used_this_period,
            "reports_remaining": max(0, sub.monthly_report_quota - sub.reports_used_this_period),
            "period_start": sub.period_start.isoformat() if sub.period_start else None,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
            "ml_refresh_hours": sub.ml_refresh_hours,
            "require_customer_phone": sub.require_customer_phone,
            "can_generate_report": quota.allowed,
            "quota_reason": quota.reason,
        }
    finally:
        session.close()


@router.post("/reports", status_code=201)
def generate_report(
    body: GenerateReportRequest,
    auth: dict = Depends(require_store_auth),
):
    """
    Generate a monthly (or custom-period) report snapshot.
    Checks subscription status + monthly quota, then runs Step 5 ML
    (run-all for this store) and Step 6 Insight Engine before writing
    the snapshot. Step 7 fills MAPE summary + MoM comparison vs prior period.
    """
    if body.period_start > body.period_end:
        raise HTTPException(status_code=400, detail="period_start must be before period_end")

    from db.models import Subscription
    from db.learning import build_mape_summary, build_mom_comparison
    from db.subscriptions import (
        check_report_quota,
        consume_report_quota,
        create_report_snapshot,
        ensure_billing_period,
    )

    session = get_session()
    try:
        sub = session.query(Subscription).filter_by(store_id=auth["store_id"]).first()
        if not sub:
            raise HTTPException(status_code=403, detail="No subscription for this store")
        ensure_billing_period(sub)
        quota = check_report_quota(sub)
        if not quota.allowed:
            raise HTTPException(status_code=402, detail=quota.reason or "Report quota denied")

        # Step 5: refresh all ML modules for this store before snapshotting
        ml_summary = None
        try:
            import os
            import sys
            if os.environ.get("SKIP_ML_ON_REPORT", "").lower() in ("1", "true", "yes"):
                ml_summary = {"skipped": True}
            else:
                ml_root = os.environ.get(
                    "ML_MODELS_ROOT",
                    os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "ml-models",
                    ),
                )
                if ml_root not in sys.path:
                    sys.path.insert(0, ml_root)
                from orchestrator import run_all_modules

                skip_forecast = os.environ.get("ML_SKIP_FORECAST_ON_REPORT", "").lower() in (
                    "1", "true", "yes",
                )
                ml_summary = run_all_modules(
                    store_id=auth["store_id"],
                    skip_forecast=skip_forecast,
                )
        except Exception as exc:
            # Report still generates; ML failure is recorded in snapshot meta
            ml_summary = {"error": str(exc)}

        # Step 6: ranked actions from ML result tables (deterministic, no LLM)
        insight_summary = None
        try:
            import os
            import sys
            if os.environ.get("SKIP_INSIGHTS_ON_REPORT", "").lower() in ("1", "true", "yes"):
                insight_summary = {"skipped": True}
            else:
                ie_root = os.environ.get(
                    "INSIGHT_ENGINE_ROOT",
                    os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "insight-engine",
                    ),
                )
                if ie_root not in sys.path:
                    sys.path.insert(0, ie_root)
                from engine import run_insight_engine

                insight_summary = run_insight_engine(
                    store_id=auth["store_id"],
                    period_start=body.period_start,
                    period_end=body.period_end,
                    persist=True,
                    session=session,
                )
        except Exception as exc:
            insight_summary = {"error": str(exc)}

        kpis = queries.get_kpis(
            session, body.period_start, body.period_end, auth["store_id"]
        )
        insights = queries.get_insights(session, auth["store_id"], limit=10)
        top_actions = [
            {
                "insight_id": i["insight_id"],
                "title": i["title"],
                "priority": i["priority"],
                "score": i["score"],
                "action_type": i.get("action_type"),
            }
            for i in insights
        ]

        # Step 7: MAPE summary + MoM vs previous snapshot
        mape_summary = build_mape_summary(session, auth["store_id"])
        mom = build_mom_comparison(
            session,
            auth["store_id"],
            period_start=body.period_start,
            period_end=body.period_end,
            period_type=body.period_type,
            current_kpis=kpis,
        )

        consume_report_quota(sub)
        snap = create_report_snapshot(
            session,
            store_id=auth["store_id"],
            period_start=body.period_start,
            period_end=body.period_end,
            period_type=body.period_type,
            kpis=kpis,
            top_actions=top_actions,
            model_versions={"ml_run": ml_summary, "insight_engine": insight_summary},
            mape_summary=mape_summary,
        )
        session.commit()
        return {
            "status": "generated",
            "snapshot_id": snap.snapshot_id,
            "store_id": auth["store_id"],
            "period_start": body.period_start.isoformat(),
            "period_end": body.period_end.isoformat(),
            "period_type": body.period_type,
            "kpis": kpis,
            "top_actions": top_actions,
            "mape_summary": mape_summary,
            "mom": mom,
            "ml_run": ml_summary,
            "insight_engine": {
                "candidates": (insight_summary or {}).get("candidates"),
                "persisted": (insight_summary or {}).get("persisted"),
                "learned_weight_multipliers": (insight_summary or {}).get(
                    "learned_weight_multipliers"
                ),
                "error": (insight_summary or {}).get("error"),
                "skipped": (insight_summary or {}).get("skipped"),
            },
            "reports_used_this_period": sub.reports_used_this_period,
            "monthly_report_quota": sub.monthly_report_quota,
        }
    except HTTPException:
        session.rollback()
        raise
    except PermissionError as exc:
        session.rollback()
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()


@router.get("/reports")
def list_reports(
    auth: dict = Depends(require_store_auth),
    limit: int = 12,
):
    """List report snapshots for the authenticated store (newest first), with MoM."""
    session = get_session()
    try:
        data = queries.get_report_snapshots(session, auth["store_id"], limit=limit)
        return {"data": data, "meta": {"store_id": auth["store_id"]}}
    finally:
        session.close()


@router.get("/insights")
def list_insights(
    auth: dict = Depends(require_store_auth),
    limit: int = 50,
    status: Optional[str] = None,
):
    """List ranked actions for the authenticated store only."""
    session = get_session()
    try:
        data = queries.get_insights(session, auth["store_id"], limit=limit, status=status)
        return {"data": data, "meta": {"store_id": auth["store_id"]}}
    finally:
        session.close()


@router.patch("/insights/{insight_id}/feedback")
def set_insight_feedback(
    insight_id: str,
    body: InsightFeedbackRequest,
    auth: dict = Depends(require_store_auth),
):
    """
    Mark an action useful / not_useful (Step 7 learning loop).
    Feedback adjusts future rule weights for that action_type.
    """
    session = get_session()
    try:
        row = queries.set_insight_feedback(
            session,
            store_id=auth["store_id"],
            insight_id=insight_id,
            feedback=body.feedback,
            status=body.status,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Insight not found")
        session.commit()
        return {"status": "updated", "insight": row}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        session.close()
