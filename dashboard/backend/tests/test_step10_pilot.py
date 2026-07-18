"""
Step 10 — Pilot: accepted-action before/after revenue, historical correlation, APIs.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def pilot_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_step10.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SKIP_ML_ON_REPORT", "1")
    monkeypatch.setenv("SKIP_INSIGHTS_ON_REPORT", "1")
    monkeypatch.setenv("SKIP_INSIGHTS_ON_NIGHTLY", "1")
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)

    _repo = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    pipeline_root = os.path.join(_repo, "invoice-pipeline")
    ml_root = os.path.join(_repo, "ml-models")
    insight_root = os.path.join(_repo, "insight-engine")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", pipeline_root)
    monkeypatch.setenv("ML_MODELS_ROOT", ml_root)
    monkeypatch.setenv("INSIGHT_ENGINE_ROOT", insight_root)
    for p in (pipeline_root, ml_root, insight_root):
        if p not in sys.path:
            sys.path.insert(0, p)

    import importlib

    import db.session as pipeline_session

    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    from common import result_models  # noqa: F401
    from db.models import Base

    Base.metadata.create_all(bind=pipeline_session.engine)

    from db.api_keys import issue_api_key
    from db.store_auth import set_store_password
    from db.plans import PLAN_BASIC, set_store_plan
    from db.models import Insight, Invoice, DailyRevenueFact, MonthlyRevenueFact

    session = pipeline_session.get_session()
    issued = issue_api_key(session, store_name="Pilot Store", plan="basic")
    set_store_password(
        session, store_id=issued.store_id, username="owner10", password="secret1"
    )
    set_store_plan(session, issued.store_id, PLAN_BASIC)
    sid = issued.store_id

    # June / July monthly facts + daily for before/after windows
    session.add(
        MonthlyRevenueFact(
            store_id=sid,
            year=2026,
            month=5,
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            revenue=10000,
            transactions=100,
            units_sold=200,
            avg_basket_value=100,
        )
    )
    session.add(
        MonthlyRevenueFact(
            store_id=sid,
            year=2026,
            month=6,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            revenue=12000,
            transactions=110,
            units_sold=220,
            avg_basket_value=109,
        )
    )
    session.add(
        MonthlyRevenueFact(
            store_id=sid,
            year=2026,
            month=7,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            revenue=15000,
            transactions=130,
            units_sold=260,
            avg_basket_value=115,
        )
    )

    # Accepted insight mid-June — baseline early June, follow-up late June
    resolved = datetime(2026, 6, 15, 10, 0, 0)
    session.add(
        Insight(
            store_id=sid,
            action_type="bundle",
            priority="high",
            title="Bundle Rice + Oil",
            body="test",
            impact_estimate=800,
            score=20,
            status="accepted",
            feedback="useful",
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            generated_at=datetime(2026, 6, 1),
            resolved_at=resolved,
        )
    )
    # Idle month May — dismissed only
    session.add(
        Insight(
            store_id=sid,
            action_type="reorder",
            priority="medium",
            title="Reorder Soap",
            body="test",
            impact_estimate=200,
            score=5,
            status="dismissed",
            feedback="not_useful",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            generated_at=datetime(2026, 5, 1),
            resolved_at=datetime(2026, 5, 5),
        )
    )

    # Daily facts around June 15 for before/after
    for day in range(1, 30):
        d = date(2026, 6, day)
        # Higher revenue after resolve day
        rev = 500 if day < 15 else 700
        session.add(
            DailyRevenueFact(
                store_id=sid,
                fact_date=d,
                revenue=rev,
                transactions=5,
                units_sold=10,
                avg_basket_value=rev / 5,
            )
        )

    # A couple of invoices (fallback path coverage)
    session.add(
        Invoice(
            store_id=sid,
            invoice_number="P-1",
            invoice_date=date(2026, 6, 10),
            grand_total=250,
            source_type="api",
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
        "ml_path",
        "ml_queries",
        "ml_api",
        "admin_api",
        "monitoring_api",
        "pilot_api",
        "scheduler",
        "tenant_auth",
        "auth_api",
        "subscription_api",
        "live_ingest_api",
        "loyalty_api",
        "main",
    ):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)

    import main

    importlib.reload(main)

    client = TestClient(main.app)
    return client, sid


def _login(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "owner10", "password": "secret1"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_revenue_window_and_outcomes(pilot_client):
    from db.session import get_session
    from db.pilot import (
        revenue_window,
        accepted_action_outcomes,
        summarize_outcomes,
        historical_action_correlation,
    )

    _client, sid = pilot_client
    session = get_session()
    try:
        win = revenue_window(session, sid, date(2026, 6, 1), date(2026, 6, 14))
        assert win["source"] == "daily_revenue_facts"
        assert win["revenue"] > 0

        outcomes = accepted_action_outcomes(
            session,
            sid,
            baseline_days=14,
            followup_days=14,
            as_of=date(2026, 7, 1),
        )
        assert len(outcomes) >= 1
        acted = [o for o in outcomes if o["action_type"] == "bundle"][0]
        assert acted["measurable"] is True
        assert acted["followup"] is not None
        assert acted["revenue_pct_change"] is not None
        # Follow-up days are ₹700 vs baseline ₹500 → positive lift
        assert acted["revenue_pct_change"] > 0

        summary = summarize_outcomes(outcomes)
        assert summary["n_acted"] >= 1
        assert summary["n_measurable"] >= 1

        corr = historical_action_correlation(session, sid, min_months=2)
        assert corr["n_months"] >= 2
        assert corr["n_acted_months"] >= 1
        # June acted → July +25%; May idle → June +20% — gap may vary but structure OK
        assert "lift_gap_pp" in corr
    finally:
        session.close()


def test_pilot_api_summary_and_outcomes(pilot_client):
    client, _sid = pilot_client
    headers = _login(client)

    r = client.get("/api/v1/pilot/summary", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcomes"]["n_acted"] >= 1
    assert "correlation" in body
    assert "suggested_festival_overrides" in body

    r2 = client.get(
        "/api/v1/pilot/outcomes",
        headers=headers,
        params={"baseline_days": 14, "followup_days": 14},
    )
    assert r2.status_code == 200, r2.text
    data = r2.json()["data"]
    assert any(o["action_type"] == "bundle" for o in data)


def test_pilot_backtest_logs_job(pilot_client):
    client, sid = pilot_client
    headers = _login(client)

    r = client.post("/api/v1/pilot/backtest", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["store_id"] == sid
    assert "verdict" in body
    assert body.get("job_id")

    jobs = client.get("/api/v1/monitoring/jobs?limit=10", headers=headers)
    assert jobs.status_code == 200
    types = [j["job_type"] for j in jobs.json()["data"]]
    assert "pilot_backtest" in types


def test_pilot_tune_bundle_grid(pilot_client, monkeypatch, tmp_path):
    """Tune endpoint needs ML association rules — seed one and dry-run grid."""
    client, sid = pilot_client
    headers = _login(client)

    from db.session import get_session
    from db.models import Product
    from common.result_models import AssociationRule

    session = get_session()
    rice = Product(product_name="Rice 5kg", category="Staples")
    oil = Product(product_name="Oil 1L", category="Staples")
    session.add_all([rice, oil])
    session.flush()
    session.add(
        AssociationRule(
            store_id=sid,
            antecedent_product_ids=[rice.product_id],
            consequent_product_ids=[oil.product_id],
            support=0.05,
            confidence=0.72,
            lift=2.1,
            suggestion_text="bundle rice oil",
        )
    )
    session.commit()
    session.close()

    # Clear rules cache so tuning sees defaults
    import config_loader

    config_loader.load_rules_config.cache_clear()

    r = client.post(
        "/api/v1/pilot/tune",
        headers=headers,
        json={"rule": "bundle", "param": "min_lift", "values": [1.0, 1.5, 3.0]},
    )
    assert r.status_code == 200, r.text
    grid = r.json()["grid"]
    assert len(grid) == 3
    # High min_lift should filter out or keep fewer candidates than low
    n_by_val = {row["value"]: row["n_candidates"] for row in grid}
    assert n_by_val[1.0] >= n_by_val[3.0]
