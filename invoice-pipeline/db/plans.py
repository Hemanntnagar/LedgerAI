"""
Subscription plan catalog (Step 2).

Plans define quotas; Subscription rows copy those values so a mid-period
plan change can be applied explicitly via set_store_plan().
"""
from __future__ import annotations

from typing import Dict, List, Optional

from db.models import Subscription, SubscriptionPlan

# Canonical plan codes used by CLI / API
PLAN_FREE = "free"
PLAN_BASIC = "basic"
PLAN_PRO = "pro"

DEFAULT_PLANS: List[Dict] = [
    {
        "plan_code": PLAN_FREE,
        "display_name": "Free",
        "monthly_report_quota": 1,
        "max_stores": 1,
        "ml_refresh_hours": 168,  # weekly
        "require_customer_phone": False,
        "features_json": {"loyalty": False, "insights": True},
    },
    {
        "plan_code": PLAN_BASIC,
        "display_name": "Basic",
        "monthly_report_quota": 4,
        "max_stores": 1,
        "ml_refresh_hours": 24,  # daily
        "require_customer_phone": False,
        "features_json": {"loyalty": False, "insights": True},
    },
    {
        "plan_code": PLAN_PRO,
        "display_name": "Pro",
        "monthly_report_quota": 30,
        "max_stores": 5,
        "ml_refresh_hours": 6,
        "require_customer_phone": True,
        "features_json": {"loyalty": True, "insights": True, "festival_ml": True},
    },
]


def seed_subscription_plans(session) -> int:
    """Upsert default plans. Returns number of rows inserted or updated."""
    touched = 0
    for spec in DEFAULT_PLANS:
        row = session.query(SubscriptionPlan).filter_by(plan_code=spec["plan_code"]).first()
        if not row:
            row = SubscriptionPlan(plan_code=spec["plan_code"])
            session.add(row)
            touched += 1
        for key, value in spec.items():
            if key == "plan_code":
                continue
            setattr(row, key, value)
        row.is_active = True
    session.flush()
    return touched


def get_plan(session, plan_code: str) -> Optional[SubscriptionPlan]:
    return session.query(SubscriptionPlan).filter_by(plan_code=plan_code, is_active=True).first()


def apply_plan_to_subscription(sub: Subscription, plan: SubscriptionPlan) -> None:
    """Copy plan quotas onto a subscription row."""
    sub.plan = plan.plan_code
    sub.monthly_report_quota = plan.monthly_report_quota
    sub.ml_refresh_hours = plan.ml_refresh_hours
    sub.require_customer_phone = bool(plan.require_customer_phone)


def set_store_plan(session, store_id: str, plan_code: str) -> Subscription:
    """Change a store's plan and refresh quota fields from the catalog."""
    plan = get_plan(session, plan_code)
    if not plan:
        raise ValueError(f"unknown or inactive plan: {plan_code}")
    sub = session.query(Subscription).filter_by(store_id=store_id).first()
    if not sub:
        raise ValueError(f"no subscription for store_id={store_id}")
    apply_plan_to_subscription(sub, plan)
    session.flush()
    return sub
