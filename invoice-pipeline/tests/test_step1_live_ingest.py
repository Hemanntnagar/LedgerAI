"""Step 1: live ingest + phone identity + API key auth."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Customer, Invoice, InvoiceLineItem, Store
from db.api_keys import issue_api_key, authenticate_api_key, hash_api_key, verify_api_key
from pipeline.live_ingest import ingest_live_invoice


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _pos_payload(**overrides):
    payload = {
        "store_name": "Corner Kirana",
        "invoice_number": "POS-1001",
        "invoice_date": "2026-07-18",
        "invoice_time": "14:30:00",
        "customer_phone": "9876543210",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "grand_total": 150,
        "line_items": [
            {
                "product_name": "Basmati Rice 5kg",
                "category": "Grains",
                "quantity": 1,
                "unit_price": 150,
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_api_key_issue_and_authenticate(session):
    issued = issue_api_key(session, store_name="Corner Kirana", plan="pro")
    session.commit()
    assert issued.api_key.startswith("inv_")
    assert verify_api_key(issued.api_key, hash_api_key(issued.api_key))

    auth = authenticate_api_key(session, issued.api_key)
    assert auth is not None
    store, sub = auth
    assert store.store_name == "Corner Kirana"
    assert sub.plan == "pro"
    assert authenticate_api_key(session, "inv_bogus") is None


def test_live_ingest_creates_rows(session):
    issued = issue_api_key(session, store_name="Corner Kirana")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    result = ingest_live_invoice(session, _pos_payload(), store=store, subscription=sub)
    session.commit()

    assert result.status == "loaded"
    assert result.invoice_id
    assert result.customer_id
    assert result.phone_normalized == "+919876543210"

    inv = session.query(Invoice).filter_by(invoice_id=result.invoice_id).one()
    assert inv.source_type == "api"
    assert inv.store_id == store.store_id
    assert float(inv.grand_total) == 150.0
    lines = session.query(InvoiceLineItem).filter_by(invoice_id=inv.invoice_id).all()
    assert len(lines) == 1


def test_same_phone_resolves_one_customer(session):
    issued = issue_api_key(session, store_name="Corner Kirana")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    r1 = ingest_live_invoice(
        session,
        _pos_payload(invoice_number="POS-1", customer_phone="9876543210"),
        store=store,
        subscription=sub,
    )
    session.flush()
    r2 = ingest_live_invoice(
        session,
        _pos_payload(
            invoice_number="POS-2",
            customer_phone="+91 98765 43210",  # same person, different format
            customer_name="Asha R",
        ),
        store=store,
        subscription=sub,
    )
    session.commit()

    assert r1.status == "loaded" and r2.status == "loaded"
    assert r1.customer_id == r2.customer_id
    assert session.query(Customer).count() == 1
    assert session.query(Invoice).count() == 2


def test_duplicate_natural_key_skipped(session):
    issued = issue_api_key(session, store_name="Corner Kirana")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    r1 = ingest_live_invoice(session, _pos_payload(), store=store, subscription=sub)
    session.flush()
    r2 = ingest_live_invoice(session, _pos_payload(), store=store, subscription=sub)
    session.commit()

    assert r1.status == "loaded"
    assert r2.status == "duplicate"
    assert session.query(Invoice).count() == 1


def test_rejects_mismatched_store_id(session):
    issued = issue_api_key(session, store_name="Corner Kirana")
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    other = Store(store_name="Other Store")
    session.add(other)
    session.flush()

    result = ingest_live_invoice(
        session,
        _pos_payload(store_id=other.store_id),
        store=store,
        subscription=store.subscription,
    )
    assert result.status == "failed_validation"
    assert "does not match" in (result.detail or "")
