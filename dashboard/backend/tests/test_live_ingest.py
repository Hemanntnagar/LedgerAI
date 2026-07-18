"""Tests for POST /api/v1/invoices live ingest."""
import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def live_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_live_ingest.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    pipeline_root = os.path.join(_repo, "invoice-pipeline")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", pipeline_root)
    if pipeline_root not in sys.path:
        sys.path.insert(0, pipeline_root)

    import importlib

    import db.session as pipeline_session
    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    from db.api_keys import issue_api_key

    session = pipeline_session.get_session()
    issued = issue_api_key(session, store_name="API Test Store", plan="basic")
    session.commit()
    session.close()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import dbconn
    importlib.reload(dbconn)
    import cache as cache_module
    importlib.reload(cache_module)
    import live_ingest_api
    importlib.reload(live_ingest_api)
    import main
    importlib.reload(main)

    return TestClient(main.app), issued


def _payload(**overrides):
    body = {
        "invoice_number": "LIVE-1",
        "invoice_date": "2026-07-18",
        "customer_phone": "9876543210",
        "grand_total": 99.0,
        "line_items": [
            {"product_name": "Tea Dust 250g", "quantity": 1, "unit_price": 99}
        ],
    }
    body.update(overrides)
    return body


def test_post_invoice_requires_api_key(live_client):
    client, _ = live_client
    r = client.post("/api/v1/invoices", json=_payload())
    assert r.status_code == 401


def test_post_invoice_creates_rows(live_client):
    client, issued = live_client
    r = client.post(
        "/api/v1/invoices",
        json=_payload(),
        headers={"X-API-Key": issued.api_key},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "loaded"
    assert data["invoice_id"]
    assert data["customer_id"]
    assert data["phone_normalized"] == "+919876543210"
    assert data["store_id"] == issued.store_id


def test_post_invoice_same_phone_one_customer(live_client):
    client, issued = live_client
    headers = {"X-API-Key": issued.api_key}

    r1 = client.post("/api/v1/invoices", json=_payload(invoice_number="A"), headers=headers)
    r2 = client.post(
        "/api/v1/invoices",
        json=_payload(invoice_number="B", customer_phone="09876543210"),
        headers=headers,
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["customer_id"] == r2.json()["customer_id"]
