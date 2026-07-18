"""Step 4 dashboard: loyalty lookup / redeem / rewards (Pro plan)."""
import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def loyalty_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_loyalty.db"
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
    from db.store_auth import set_store_password
    from db.loyalty import clear_loyalty_config_cache
    from pipeline.live_ingest import ingest_live_invoice
    from db.models import Store

    clear_loyalty_config_cache()

    session = pipeline_session.get_session()
    issued = issue_api_key(session, store_name="Loyalty API Store", plan="pro")
    set_store_password(
        session, store_id=issued.store_id, username="loyalty_owner", password="secret1"
    )
    store = session.query(Store).filter_by(store_id=issued.store_id).one()
    sub = store.subscription

    # Seed two purchases so balance is 150 points
    ingest_live_invoice(
        session,
        {
            "invoice_number": "L-1",
            "invoice_date": "2026-07-01",
            "customer_phone": "9876543210",
            "customer_name": "Asha",
            "grand_total": 1000,
            "line_items": [
                {"product_name": "Rice", "quantity": 1, "unit_price": 1000}
            ],
        },
        store=store,
        subscription=sub,
    )
    ingest_live_invoice(
        session,
        {
            "invoice_number": "L-2",
            "invoice_date": "2026-07-02",
            "customer_phone": "9876543210",
            "grand_total": 500,
            "line_items": [
                {"product_name": "Oil", "quantity": 1, "unit_price": 500}
            ],
        },
        store=store,
        subscription=sub,
    )
    session.commit()
    api_key = issued.api_key
    store_id = issued.store_id
    session.close()

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    import dbconn
    importlib.reload(dbconn)
    for mod_name in (
        "cache", "queries", "tenant_auth", "auth_api", "subscription_api",
        "live_ingest_api", "loyalty_api", "main",
    ):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)

    import main
    importlib.reload(main)

    return TestClient(main.app), api_key, store_id


def test_lookup_and_rewards_with_api_key(loyalty_client):
    client, api_key, store_id = loyalty_client
    headers = {"X-API-Key": api_key}

    lookup = client.get("/api/v1/loyalty/lookup", params={"phone": "9876543210"}, headers=headers)
    assert lookup.status_code == 200, lookup.text
    body = lookup.json()
    assert body["store_id"] == store_id
    assert body["phone_normalized"] == "+919876543210"
    assert body["points_balance"] == 150
    assert body["visits"] == 2
    assert body["tier"] in ("bronze", "silver", "gold")

    rewards = client.get("/api/v1/loyalty/rewards", headers=headers)
    assert rewards.status_code == 200
    codes = {r["code"] for r in rewards.json()["rewards"]}
    assert "DISCOUNT5" in codes


def test_redeem_via_session(loyalty_client):
    client, _api_key, store_id = loyalty_client
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "loyalty_owner", "password": "secret1"},
    )
    assert login.status_code == 200
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    redeem = client.post(
        "/api/v1/loyalty/redeem",
        headers=headers,
        json={"phone": "9876543210", "reward_code": "DISCOUNT5"},
    )
    assert redeem.status_code == 200, redeem.text
    data = redeem.json()
    assert data["status"] == "redeemed"
    assert data["points_balance"] == 50
    assert data["reward"]["code"] == "DISCOUNT5"
    assert data["store_id"] == store_id

    # Insufficient for second same reward
    again = client.post(
        "/api/v1/loyalty/redeem",
        headers=headers,
        json={"phone": "9876543210", "reward_code": "DISCOUNT5"},
    )
    assert again.status_code == 402


def test_basic_plan_forbidden(monkeypatch, tmp_path):
    db_path = tmp_path / "test_loyalty_basic.db"
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
    issued = issue_api_key(session, store_name="No Loyalty Store", plan="basic")
    session.commit()
    api_key = issued.api_key
    session.close()

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    import dbconn
    importlib.reload(dbconn)
    for mod_name in (
        "cache", "queries", "tenant_auth", "auth_api", "subscription_api",
        "live_ingest_api", "loyalty_api", "main",
    ):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    r = client.get(
        "/api/v1/loyalty/lookup",
        params={"phone": "9876543210"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 403
