"""
Step 4 — phone loyalty: earn on repeat purchase, VIP tier, festival bonus, redeem.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import (
    Base,
    Customer,
    FestivalEvent,
    Invoice,
    LoyaltyAccount,
    LoyaltyTransaction,
    Store,
)
from db.api_keys import issue_api_key
from db.loyalty import (
    clear_loyalty_config_cache,
    compute_tier,
    earn_on_invoice,
    lookup_by_phone,
    points_for_amount,
    redeem_reward,
)
from db.plans import seed_subscription_plans
from db.phone import normalize_phone
from pipeline.live_ingest import ingest_live_invoice


@pytest.fixture
def session():
    clear_loyalty_config_cache()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed_subscription_plans(s)
    s.commit()
    yield s
    s.close()
    clear_loyalty_config_cache()


def _pos_payload(**overrides):
    payload = {
        "store_name": "Loyalty Kirana",
        "invoice_number": "POS-L1",
        "invoice_date": "2026-07-18",
        "customer_phone": "9876543210",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "grand_total": 1000,
        "line_items": [
            {
                "product_name": "Basmati Rice 5kg",
                "category": "Grains",
                "quantity": 1,
                "unit_price": 1000,
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_points_math_and_festival_multiplier(session):
    # ₹1000 / ₹10 = 100 points
    pts, mult, fest = points_for_amount(1000.0)
    assert pts == 100
    assert mult == 1.0
    assert fest is None

    session.add(
        FestivalEvent(
            name="Test Fest",
            year=2026,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            region="india",
            category="religious",
        )
    )
    session.commit()
    pts2, mult2, fest2 = points_for_amount(
        1000.0, invoice_date=date(2026, 7, 18), session=session
    )
    assert mult2 == 2.0
    assert pts2 == 200
    assert fest2 == "Test Fest"


def test_tier_thresholds():
    assert compute_tier(0, 0) == "bronze"
    assert compute_tier(5000, 0) == "silver"
    assert compute_tier(0, 5) == "silver"
    assert compute_tier(20000, 0) == "gold"
    assert compute_tier(100, 15) == "gold"


def test_second_purchase_accrues_points(session):
    issued = issue_api_key(session, store_name="Loyalty Kirana", plan="pro")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    r1 = ingest_live_invoice(
        session, _pos_payload(invoice_number="POS-L1", grand_total=1000),
        store=store, subscription=sub,
    )
    session.commit()
    assert r1.status == "loaded"
    assert r1.loyalty_points_earned == 100
    assert r1.loyalty_points_balance == 100
    assert r1.loyalty_tier == "bronze"

    r2 = ingest_live_invoice(
        session,
        _pos_payload(
            invoice_number="POS-L2",
            grand_total=500,
            line_items=[{
                "product_name": "Mustard Oil 1L",
                "category": "Oils",
                "quantity": 1,
                "unit_price": 500,
            }],
        ),
        store=store,
        subscription=sub,
    )
    session.commit()
    assert r2.status == "loaded"
    assert r2.loyalty_points_earned == 50
    assert r2.loyalty_points_balance == 150

    acct = session.query(LoyaltyAccount).filter_by(store_id=store.store_id).one()
    assert acct.points_balance == 150
    txns = session.query(LoyaltyTransaction).filter_by(
        loyalty_account_id=acct.loyalty_account_id, reason="earn"
    ).all()
    assert len(txns) == 2

    # Same phone → one customer
    customers = session.query(Customer).filter_by(
        phone_normalized=normalize_phone("9876543210")
    ).all()
    assert len(customers) == 1


def test_vip_tier_flips_at_threshold(session):
    issued = issue_api_key(session, store_name="VIP Store", plan="pro")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    # First bill pushes lifetime spend to ₹20,000 → gold (VIP)
    r = ingest_live_invoice(
        session,
        _pos_payload(
            store_name="VIP Store",
            invoice_number="VIP-1",
            grand_total=20000,
            line_items=[{
                "product_name": "Bulk Oil",
                "category": "Oils",
                "quantity": 1,
                "unit_price": 20000,
            }],
        ),
        store=store,
        subscription=sub,
    )
    session.commit()
    assert r.status == "loaded"
    assert r.loyalty_tier == "gold"
    assert r.loyalty_points_earned == 2000

    lookup = lookup_by_phone(session, store.store_id, "9876543210")
    assert lookup.found
    assert lookup.tier == "gold"
    assert lookup.lifetime_spend == 20000.0
    assert lookup.visits == 1


def test_basic_plan_skips_earn(session):
    issued = issue_api_key(session, store_name="Basic Store", plan="basic")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    r = ingest_live_invoice(
        session,
        _pos_payload(store_name="Basic Store", invoice_number="B-1"),
        store=store,
        subscription=sub,
    )
    session.commit()
    assert r.status == "loaded"
    assert r.loyalty_points_earned is None
    assert session.query(LoyaltyAccount).count() == 0


def test_redeem_reward(session):
    issued = issue_api_key(session, store_name="Redeem Store", plan="pro")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    ingest_live_invoice(
        session,
        _pos_payload(
            store_name="Redeem Store",
            invoice_number="R-1",
            grand_total=1500,
            line_items=[{
                "product_name": "Ghee",
                "category": "Dairy",
                "quantity": 1,
                "unit_price": 1500,
            }],
        ),
        store=store,
        subscription=sub,
    )
    session.commit()

    ok = redeem_reward(
        session, store_id=store.store_id, phone="9876543210", reward_code="DISCOUNT5"
    )
    session.commit()
    assert ok.status == "redeemed"
    assert ok.points_delta == -100
    assert ok.points_balance == 50  # 150 earned - 100
    assert ok.reward["code"] == "DISCOUNT5"

    fail = redeem_reward(
        session, store_id=store.store_id, phone="9876543210", reward_code="DISCOUNT5"
    )
    assert fail.status == "failed"
    assert "insufficient" in (fail.detail or "")


def test_earn_idempotent_per_invoice(session):
    issued = issue_api_key(session, store_name="Idem Store", plan="pro")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription
    r = ingest_live_invoice(
        session,
        _pos_payload(store_name="Idem Store", invoice_number="I-1"),
        store=store,
        subscription=sub,
    )
    session.commit()
    inv = session.query(Invoice).filter_by(invoice_id=r.invoice_id).one()
    again = earn_on_invoice(session, inv)
    assert again.status == "duplicate"
    assert session.query(LoyaltyTransaction).count() == 1
