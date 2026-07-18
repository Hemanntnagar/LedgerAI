"""
Step 7 — Monthly report + MoM learning loop.

Exit criteria: two consecutive months produce snapshots; second shows MoM
delta; insight feedback adjusts future rule weights.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def learning_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_step7.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SKIP_ML_ON_REPORT", "1")
    monkeypatch.setenv("SKIP_INSIGHTS_ON_REPORT", "1")
    _repo = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
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
    from db.plans import PLAN_BASIC, set_store_plan

    session = pipeline_session.get_session()
    issued = issue_api_key(session, store_name="Learning Store", plan="basic")
    set_store_password(
        session, store_id=issued.store_id, username="owner_l", password="secret1"
    )
    set_store_plan(session, issued.store_id, PLAN_BASIC)  # quota 4

    product = Product(product_name="Widget", category="Cat")
    session.add(product)
    session.flush()

    # June + July revenue (different totals for MoM)
    for inv_num, inv_date, total in (
        ("J-1", date(2026, 6, 5), 200.0),
        ("J-2", date(2026, 6, 20), 100.0),
        ("L-1", date(2026, 7, 5), 500.0),
    ):
        inv = Invoice(
            store_id=issued.store_id,
            invoice_number=inv_num,
            invoice_date=inv_date,
            grand_total=total,
            source_type="csv",
        )
        session.add(inv)
        session.flush()
        session.add(
            InvoiceLineItem(
                invoice_id=inv.invoice_id,
                product_id=product.product_id,
                quantity=1,
                unit_price=total,
                line_total=total,
            )
        )

    # Prior-period insight with feedback (action outcomes for MoM)
    session.add(
        Insight(
            store_id=issued.store_id,
            action_type="bundle",
            priority="high",
            title="June bundle",
            body="try it",
            score=10,
            feedback="useful",
            status="accepted",
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
        )
    )
    session.add(
        Insight(
            store_id=issued.store_id,
            action_type="stop_discount",
            priority="medium",
            title="June stop discount",
            body="skip",
            score=5,
            feedback="not_useful",
            status="dismissed",
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
        )
    )
    session.commit()
    session.close()

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    import dbconn
    importlib.reload(dbconn)
    for mod_name in (
        "cache",
        "queries",
        "tenant_auth",
        "auth_api",
        "subscription_api",
        "live_ingest_api",
        "main",
    ):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)

    import main
    importlib.reload(main)

    return TestClient(main.app), issued


def _login(client):
    r = client.post(
        "/api/v1/auth/login", json={"username": "owner_l", "password": "secret1"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_two_months_produce_mom_delta(learning_client):
    client, issued = learning_client
    headers = _login(client)

    june = client.post(
        "/api/v1/reports",
        json={
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
            "period_type": "monthly",
        },
        headers=headers,
    )
    assert june.status_code == 201, june.text
    j = june.json()
    assert j["kpis"]["total_revenue"] == 300.0
    assert j["mom"] is None  # first month — no prior snapshot
    assert "mape_summary" in j
    assert j["mape_summary"]["available"] is False  # ML skipped

    july = client.post(
        "/api/v1/reports",
        json={
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "period_type": "monthly",
        },
        headers=headers,
    )
    assert july.status_code == 201, july.text
    y = july.json()
    assert y["kpis"]["total_revenue"] == 500.0
    assert y["mom"] is not None
    assert y["mom"]["previous_snapshot_id"] == j["snapshot_id"]
    rev = y["mom"]["kpi_deltas"]["total_revenue"]
    assert rev["previous"] == 300.0
    assert rev["current"] == 500.0
    assert rev["delta"] == 200.0
    assert rev["pct_change"] == pytest.approx(66.67, abs=0.01)
    outcomes = y["mom"]["previous_action_outcomes"]
    assert outcomes["useful"] == 1
    assert outcomes["not_useful"] == 1

    listed = client.get("/api/v1/reports", headers=headers)
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert len(data) == 2
    assert data[0]["period_start"] == "2026-07-01"
    assert data[0]["mom"] is not None
    assert data[1]["mom"] is None


def test_insight_feedback_endpoint(learning_client):
    client, issued = learning_client
    headers = _login(client)

    # Seed a fresh insight for July
    from db.session import get_session
    from db.models import Insight

    session = get_session()
    insight = Insight(
        store_id=issued.store_id,
        action_type="bundle",
        priority="high",
        title="July action",
        body="do it",
        score=20,
        status="new",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
    )
    session.add(insight)
    session.commit()
    iid = insight.insight_id
    session.close()

    r = client.patch(
        f"/api/v1/insights/{iid}/feedback",
        json={"feedback": "useful", "status": "accepted"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["insight"]["feedback"] == "useful"
    assert r.json()["insight"]["status"] == "accepted"

    bad = client.patch(
        f"/api/v1/insights/{iid}/feedback",
        json={"feedback": "maybe"},
        headers=headers,
    )
    assert bad.status_code == 400
