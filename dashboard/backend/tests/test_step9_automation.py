"""
Step 9 — Automation & monitoring: job_runs, MAPE/ingestion alerts, nightly due logic.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mon_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_step9.db"
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
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", pipeline_root)
    monkeypatch.setenv("ML_MODELS_ROOT", ml_root)
    if pipeline_root not in sys.path:
        sys.path.insert(0, pipeline_root)
    if ml_root not in sys.path:
        sys.path.insert(0, ml_root)

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
    from db.models import Product
    from common.result_models import ForecastResult

    session = pipeline_session.get_session()
    issued = issue_api_key(session, store_name="Mon Store", plan="basic")
    set_store_password(
        session, store_id=issued.store_id, username="owner9", password="secret1"
    )
    set_store_plan(session, issued.store_id, PLAN_BASIC)

    product = Product(product_name="Rice 1kg", category="Staples")
    session.add(product)
    session.flush()

    # Mostly low-confidence forecasts → confidence_collapse
    for i in range(5):
        session.add(
            ForecastResult(
                store_id=issued.store_id,
                product_id=product.product_id,
                forecast_date=date(2026, 8, 1 + i),
                predicted_units=10,
                predicted_revenue=400,
                confidence_level="low",
                backtest_mape_pct=55.0,
                grain="weekly",
                model_version="test",
            )
        )
    session.commit()
    store_id = issued.store_id
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
    return client, store_id


def _login(client):
    r = client.post(
        "/api/v1/auth/login", json={"username": "owner9", "password": "secret1"}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_job_run_table_and_helpers(mon_client):
    client, store_id = mon_client
    from db.session import get_session
    from db.jobs import start_job, finish_job, last_successful_job, job_to_dict

    session = get_session()
    job = start_job(session, "ml_refresh", store_id=store_id, triggered_by="test")
    finish_job(session, job, status="success", detail={"ok": True})
    session.commit()
    last = last_successful_job(session, "ml_refresh", store_id=store_id)
    assert last is not None
    assert last.job_id == job.job_id
    d = job_to_dict(last)
    assert d["status"] == "success"
    assert d["detail"]["ok"] is True
    session.close()


def test_ingestion_health_and_mape_alerts(mon_client):
    client, store_id = mon_client
    from db.session import get_session
    from db.models import IngestionError
    from db.monitoring import check_ingestion_health, check_mape_drift, list_alerts

    session = get_session()
    # Seed enough errors to trip rate (no successful invoices in window)
    for i in range(6):
        session.add(
            IngestionError(
                source_file_path=f"x{i}",
                error_type="validation",
                error_detail="bad",
                created_at=datetime.utcnow(),
            )
        )
    session.commit()

    ing = check_ingestion_health(session, raise_alerts=True)
    assert ing["n_errors"] >= 6
    assert ing["healthy"] is False
    assert ing["alert_raised"] is True

    mape = check_mape_drift(session, store_id, raise_alerts=True)
    assert "confidence_collapse" in mape["fired"]
    assert "mape_high" in mape["fired"]
    assert mape["healthy"] is False

    alerts = list_alerts(session, store_id=store_id, status="open")
    types = {a.alert_type for a in alerts}
    assert "confidence_collapse" in types or "mape_high" in types
    assert any(a.alert_type == "ingestion_errors" for a in alerts)
    session.close()


def test_stores_due_respects_ml_refresh_hours(mon_client):
    client, store_id = mon_client
    from db.session import get_session
    from db.jobs import start_job, finish_job
    from nightly import stores_due_for_refresh

    session = get_session()
    # Fresh success → not due (basic plan = 24h)
    job = start_job(session, "ml_refresh", store_id=store_id, triggered_by="test")
    finish_job(session, job, status="success")
    session.commit()

    due = stores_due_for_refresh(session, force=False)
    assert store_id not in [s.store_id for s in due]

    # Age the job past refresh window
    job.finished_at = datetime.utcnow() - timedelta(hours=25)
    session.commit()
    due2 = stores_due_for_refresh(session, force=False)
    assert store_id in [s.store_id for s in due2]

    due_force = stores_due_for_refresh(session, force=True)
    assert store_id in [s.store_id for s in due_force]
    session.close()


def test_monitoring_api_endpoints(mon_client):
    client, store_id = mon_client
    headers = _login(client)

    h = client.get("/api/v1/monitoring/health", headers=headers)
    assert h.status_code == 200
    body = h.json()
    assert body["store_id"] == store_id
    assert "ingestion" in body
    assert "mape" in body

    # Trigger alert then list/ack
    from db.session import get_session
    from db.monitoring import check_mape_drift

    session = get_session()
    check_mape_drift(session, store_id, raise_alerts=True)
    session.commit()
    session.close()

    alerts = client.get("/api/v1/monitoring/alerts", headers=headers)
    assert alerts.status_code == 200
    data = alerts.json()["data"]
    assert len(data) >= 1
    aid = data[0]["alert_id"]

    ack = client.post(
        f"/api/v1/monitoring/alerts/{aid}/ack",
        headers=headers,
        json={"status": "acknowledged"},
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"

    jobs = client.get("/api/v1/monitoring/jobs", headers=headers)
    assert jobs.status_code == 200
    assert "data" in jobs.json()


def test_manual_refresh_logs_job(mon_client, monkeypatch):
    client, store_id = mon_client
    headers = _login(client)

    from db.session import get_session, init_db
    from db.jobs import start_job, finish_job

    def _fake_refresh(sid, **kw):
        init_db()
        session = get_session()
        job = start_job(session, "ml_refresh", store_id=sid, triggered_by="api")
        finish_job(session, job, status="success", detail={"ml": {"abc": {"n": 0}}})
        session.commit()
        jid = job.job_id
        session.close()
        return {"job_id": jid, "store_id": sid, "status": "success", "detail": {}}

    monkeypatch.setattr("nightly.refresh_store", _fake_refresh)

    r = client.post("/api/v1/monitoring/refresh?skip_forecast=true", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert r.json()["job_id"]

    jobs = client.get("/api/v1/monitoring/jobs?job_type=ml_refresh", headers=headers)
    assert any(j["status"] == "success" for j in jobs.json()["data"])
