"""Step 2: plans, store auth, report quotas."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Store, Subscription
from db.plans import PLAN_FREE, PLAN_PRO, seed_subscription_plans, set_store_plan
from db.api_keys import issue_api_key
from db.store_auth import login, set_store_password, authenticate_session
from db.subscriptions import check_report_quota, consume_report_quota, ensure_billing_period


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_subscription_plans(s)
    s.commit()
    yield s
    s.close()


def test_plans_seeded(session):
    from db.models import SubscriptionPlan

    codes = {p.plan_code for p in session.query(SubscriptionPlan).all()}
    assert {"free", "basic", "pro"} <= codes


def test_issue_api_key_applies_plan_quotas(session):
    issued = issue_api_key(session, store_name="A", plan="pro")
    sub = session.query(Subscription).filter_by(store_id=issued.store_id).one()
    assert sub.plan == "pro"
    assert sub.monthly_report_quota == 30
    assert sub.require_customer_phone is True


def test_set_plan_refreshes_quotas(session):
    issued = issue_api_key(session, store_name="A", plan="basic")
    sub = set_store_plan(session, issued.store_id, PLAN_FREE)
    assert sub.monthly_report_quota == 1
    sub = set_store_plan(session, issued.store_id, PLAN_PRO)
    assert sub.monthly_report_quota == 30


def test_store_login_and_session(session):
    issued = issue_api_key(session, store_name="Corner", plan="basic")
    set_store_password(
        session, store_id=issued.store_id, username="owner", password="secret1"
    )
    session.commit()

    ctx = login(session, username="owner", password="secret1")
    assert ctx.raw_token.startswith("st_")
    assert ctx.store.store_id == issued.store_id
    session.commit()

    again = authenticate_session(session, ctx.raw_token)
    assert again is not None
    assert again.store.store_id == issued.store_id

    assert authenticate_session(session, "st_bogus") is None


def test_report_quota_blocks_then_allows_after_reset(session):
    issued = issue_api_key(session, store_name="A", plan="free")
    sub = session.query(Subscription).filter_by(store_id=issued.store_id).one()
    ensure_billing_period(sub, today=date(2026, 7, 15))
    assert sub.monthly_report_quota == 1

    assert check_report_quota(sub, today=date(2026, 7, 15)).allowed
    consume_report_quota(sub, today=date(2026, 7, 15))
    assert not check_report_quota(sub, today=date(2026, 7, 15)).allowed

    # New month resets
    ensure_billing_period(sub, today=date(2026, 8, 1))
    assert sub.reports_used_this_period == 0
    assert check_report_quota(sub, today=date(2026, 8, 1)).allowed


def test_inactive_subscription_blocks_reports(session):
    issued = issue_api_key(session, store_name="A", plan="pro")
    sub = session.query(Subscription).filter_by(store_id=issued.store_id).one()
    sub.status = "cancelled"
    q = check_report_quota(sub)
    assert not q.allowed
    assert "cancelled" in (q.reason or "")
