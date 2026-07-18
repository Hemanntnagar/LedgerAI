"""Tests for Step 0: phone normalization, invoice contract, schema create."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.phone import InvalidPhoneError, normalize_phone, phones_match
from db.models import (
    Base, Customer, Store, Offer, Invoice, InvoiceLineItem, InvoiceOffer,
    Subscription, LoyaltyAccount, LoyaltyTransaction, FestivalEvent,
    Insight, ReportSnapshot, Product,
)
from db.seed_festivals import seed_festival_events
from contracts.invoice_contract import validate_invoice_contract


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("91-9876543210", "+919876543210"),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_phone_ok(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "5123456789", "abcdefghij", "+1 415 555 2671"])
def test_normalize_phone_bad(raw):
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


def test_phones_match():
    assert phones_match("9876543210", "+91 98765 43210")
    assert not phones_match("9876543210", "9876543211")


def test_invoice_contract_requires_store_and_lines():
    with pytest.raises(Exception):
        validate_invoice_contract({
            "invoice_number": "INV-1",
            "invoice_date": "2026-07-01",
            "grand_total": 100,
            "line_items": [{"product_name": "Rice", "quantity": 1, "unit_price": 100}],
        })


def test_invoice_contract_normalizes_phone():
    inv = validate_invoice_contract({
        "store_name": "Demo Store",
        "invoice_number": "INV-1",
        "invoice_date": "2026-07-01",
        "grand_total": 100,
        "customer_phone": "9876543210",
        "line_items": [{"product_name": "Rice", "quantity": 1, "unit_price": 100}],
    })
    assert inv.phone_normalized == "+919876543210"


def test_invoice_contract_require_phone():
    payload = {
        "store_name": "Demo Store",
        "invoice_number": "INV-1",
        "invoice_date": "2026-07-01",
        "grand_total": 100,
        "line_items": [{"product_name": "Rice", "quantity": 1, "unit_price": 100}],
    }
    with pytest.raises(ValueError, match="customer_phone"):
        validate_invoice_contract(payload, require_phone=True)


def test_schema_create_and_festival_seed(tmp_path):
    db_path = tmp_path / "step0.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # All Step 0 tables exist
    for table in (
        "customers", "offers", "invoice_offers", "subscriptions",
        "loyalty_accounts", "loyalty_transactions", "festival_events",
        "insights", "report_snapshots",
    ):
        assert table in Base.metadata.tables

    store = Store(store_name="Test Kirana")
    session.add(store)
    session.flush()

    customer = Customer(
        customer_name="Asha",
        phone="9876543210",
        phone_normalized="+919876543210",
    )
    session.add(customer)
    session.flush()

    offer = Offer(store_id=store.store_id, offer_code="FEST10", offer_name="Festival 10%")
    session.add(offer)
    session.flush()

    product = Product(product_name="Basmati Rice", unit_cost=Decimal("40.00"), stock_qty=Decimal("100"))
    session.add(product)
    session.flush()

    inv = Invoice(
        store_id=store.store_id,
        customer_id=customer.customer_id,
        invoice_number="INV-100",
        invoice_date=date(2026, 7, 1),
        grand_total=Decimal("90.00"),
        source_type="api",
    )
    session.add(inv)
    session.flush()
    session.add(InvoiceLineItem(
        invoice_id=inv.invoice_id,
        product_id=product.product_id,
        quantity=Decimal("2"),
        unit_price=Decimal("50"),
        discount_pct=Decimal("10"),
        line_total=Decimal("90"),
        offer_id=offer.offer_id,
    ))
    session.add(InvoiceOffer(invoice_id=inv.invoice_id, offer_id=offer.offer_id))
    session.add(Subscription(store_id=store.store_id, plan="pro", require_customer_phone=True))
    acct = LoyaltyAccount(store_id=store.store_id, customer_id=customer.customer_id, points_balance=50)
    session.add(acct)
    session.flush()
    session.add(LoyaltyTransaction(
        loyalty_account_id=acct.loyalty_account_id,
        invoice_id=inv.invoice_id,
        points_delta=50,
        reason="earn",
    ))
    session.add(Insight(
        store_id=store.store_id,
        action_type="bundle",
        priority="medium",
        title="Bundle Rice + Oil",
        body="Test insight",
        status="new",
    ))
    session.add(ReportSnapshot(
        store_id=store.store_id,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        period_type="monthly",
        kpis_json={"revenue": 1000},
    ))
    session.commit()

    inserted = seed_festival_events(session)
    assert inserted > 0
    assert session.query(FestivalEvent).count() == inserted
    # idempotent
    assert seed_festival_events(session) == 0

    # unique phone
    session.add(Customer(phone_normalized="+919876543210"))
    with pytest.raises(Exception):
        session.commit()
    session.rollback()
    session.close()
