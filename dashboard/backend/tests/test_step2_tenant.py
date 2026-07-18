"""Step 2 dashboard: store isolation + report quota gate."""
import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def tenant_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_tenant.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SKIP_ML_ON_REPORT", "1")  # Step 5 ML covered in ml-models tests
    monkeypatch.setenv("SKIP_INSIGHTS_ON_REPORT", "1")  # Step 6 covered in insight-engine tests
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
    from db.models import Invoice, InvoiceLineItem, Insight, Product, Store
    from db.plans import set_store_plan, PLAN_FREE

    session = pipeline_session.get_session()

    a = issue_api_key(session, store_name="Store A", plan="basic")
    b = issue_api_key(session, store_name="Store B", plan="basic")
    set_store_password(session, store_id=a.store_id, username="owner_a", password="secret1")
    set_store_password(session, store_id=b.store_id, username="owner_b", password="secret1")
    set_store_plan(session, a.store_id, PLAN_FREE)  # quota = 1 for report tests

    product = Product(product_name="Widget", category="Cat")
    session.add(product)
    session.flush()

    inv_a = Invoice(
        store_id=a.store_id, invoice_number="A-1", invoice_date=date(2026, 7, 1),
        grand_total=100.0, source_type="csv",
    )
    inv_b = Invoice(
        store_id=b.store_id, invoice_number="B-1", invoice_date=date(2026, 7, 1),
        grand_total=999.0, source_type="csv",
    )
    session.add_all([inv_a, inv_b])
    session.flush()
    session.add(InvoiceLineItem(
        invoice_id=inv_a.invoice_id, product_id=product.product_id,
        quantity=1, unit_price=100, line_total=100,
    ))
    session.add(InvoiceLineItem(
        invoice_id=inv_b.invoice_id, product_id=product.product_id,
        quantity=1, unit_price=999, line_total=999,
    ))
    session.add(Insight(
        store_id=a.store_id, action_type="bundle", priority="high",
        title="Action A", body="only A", score=10,
    ))
    session.add(Insight(
        store_id=b.store_id, action_type="bundle", priority="high",
        title="Action B", body="only B", score=10,
    ))
    session.commit()
    session.close()

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    import dbconn
    importlib.reload(dbconn)
    for mod_name in ("cache", "queries", "tenant_auth", "auth_api", "subscription_api", "live_ingest_api", "main"):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)

    import main
    importlib.reload(main)

    return TestClient(main.app), a, b


def test_login_and_me(tenant_client):
    client, a, _b = tenant_client
    r = client.post("/api/v1/auth/login", json={"username": "owner_a", "password": "secret1"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["store_id"] == a.store_id

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["store_id"] == a.store_id
    assert me.json()["username"] == "owner_a"


def test_store_a_cannot_see_store_b_kpis(tenant_client):
    client, a, b = tenant_client
    login = client.post("/api/v1/auth/login", json={"username": "owner_a", "password": "secret1"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Own store revenue
    r = client.get(
        "/api/kpis?start_date=2026-07-01&end_date=2026-07-31",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["total_revenue"] == 100.0
    assert r.json()["meta"]["store_id"] == a.store_id

    # Explicit other store → 403
    r2 = client.get(
        f"/api/kpis?start_date=2026-07-01&end_date=2026-07-31&store_id={b.store_id}",
        headers=headers,
    )
    assert r2.status_code == 403

    # Stores list is scoped
    stores = client.get("/api/stores", headers=headers)
    assert stores.status_code == 200
    ids = [s["store_id"] for s in stores.json()["data"]]
    assert ids == [a.store_id]


def test_insights_isolated(tenant_client):
    client, _a, _b = tenant_client
    token = client.post(
        "/api/v1/auth/login", json={"username": "owner_a", "password": "secret1"}
    ).json()["token"]
    r = client.get("/api/v1/insights", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    titles = [i["title"] for i in r.json()["data"]]
    assert titles == ["Action A"]


def test_report_requires_auth_and_checks_quota(tenant_client):
    client, _a, _b = tenant_client
    body = {
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
        "period_type": "monthly",
    }
    assert client.post("/api/v1/reports", json=body).status_code == 401

    token = client.post(
        "/api/v1/auth/login", json={"username": "owner_a", "password": "secret1"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r1 = client.post("/api/v1/reports", json=body, headers=headers)
    assert r1.status_code == 201, r1.text
    assert r1.json()["kpis"]["total_revenue"] == 100.0
    assert r1.json()["reports_used_this_period"] == 1

    # Free plan quota = 1 → second report blocked
    r2 = client.post("/api/v1/reports", json=body, headers=headers)
    assert r2.status_code == 402
    assert "quota" in r2.json()["detail"].lower()

    sub = client.get("/api/v1/subscription", headers=headers)
    assert sub.status_code == 200
    assert sub.json()["can_generate_report"] is False
