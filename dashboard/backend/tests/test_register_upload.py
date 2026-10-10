"""Multi-store register + browser invoice upload + tenant isolation."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    db_path = tmp_path / "test_register.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SKIP_ML_ON_REPORT", "1")
    monkeypatch.setenv("SKIP_INSIGHTS_ON_REPORT", "1")
    monkeypatch.setenv("ANALYTICS_ASYNC", "0")
    _repo = Path(__file__).resolve().parents[3]
    pipeline_root = str(_repo / "invoice-pipeline")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", pipeline_root)
    monkeypatch.setenv("ML_MODELS_ROOT", str(_repo / "ml-models"))
    monkeypatch.setenv("INSIGHT_ENGINE_ROOT", str(_repo / "insight-engine"))
    if pipeline_root not in sys.path:
        sys.path.insert(0, pipeline_root)

    import importlib

    import db.session as pipeline_session

    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    backend_root = str(Path(__file__).resolve().parents[1])
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
        "upload_api",
        "monitoring_api",
        "main",
    ):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)

    import main

    importlib.reload(main)
    return TestClient(main.app), _repo


def test_register_login_and_duplicate(app_client):
    client, _repo = app_client
    r = client.post(
        "/api/v1/auth/register",
        json={
            "store_name": "Alpha Kirana",
            "username": "alpha_owner",
            "password": "secret12",
            "location": "Pune",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["store_name"] == "Alpha Kirana"
    assert body["username"] == "alpha_owner"
    assert body["token"]
    token = body["token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["store_name"] == "Alpha Kirana"

    dup = client.post(
        "/api/v1/auth/register",
        json={
            "store_name": "Alpha Kirana",
            "username": "other",
            "password": "secret12",
        },
    )
    assert dup.status_code == 400

    dup_user = client.post(
        "/api/v1/auth/register",
        json={
            "store_name": "Beta Kirana",
            "username": "alpha_owner",
            "password": "secret12",
        },
    )
    assert dup_user.status_code == 400


def test_upload_forces_store_and_isolates(app_client):
    client, repo = app_client
    sample = repo / "invoice-pipeline" / "tests" / "fixtures" / "sample_invoices.csv"
    assert sample.is_file()

    a = client.post(
        "/api/v1/auth/register",
        json={"store_name": "Store Upload A", "username": "up_a", "password": "secret12"},
    )
    assert a.status_code == 200, a.text
    token_a = a.json()["token"]
    store_a = a.json()["store_id"]

    b = client.post(
        "/api/v1/auth/register",
        json={"store_name": "Store Upload B", "username": "up_b", "password": "secret12"},
    )
    assert b.status_code == 200, b.text
    token_b = b.json()["token"]

    with open(sample, "rb") as fh:
        up = client.post(
            "/api/v1/uploads/invoices",
            headers={"Authorization": f"Bearer {token_a}"},
            files={"file": ("sample_invoices.csv", fh, "text/csv")},
        )
    assert up.status_code == 200, up.text
    data = up.json()
    assert data["store_id"] == store_a
    assert data["invoices_loaded"] >= 1

    # Store B must not see Store A KPIs
    from datetime import date

    start, end = "2026-05-01", "2026-05-31"
    kpis_b = client.get(
        f"/api/kpis?start_date={start}&end_date={end}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert kpis_b.status_code == 200
    # Empty or zero revenue for B
    rev_b = (kpis_b.json().get("data") or {}).get("total_revenue") or 0
    assert float(rev_b) == 0.0

    kpis_a = client.get(
        f"/api/kpis?start_date={start}&end_date={end}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert kpis_a.status_code == 200
    rev_a = (kpis_a.json().get("data") or {}).get("total_revenue") or 0
    assert float(rev_a) > 0


def test_refresh_queues_when_async(app_client, monkeypatch):
    client, _repo = app_client
    monkeypatch.setenv("ANALYTICS_ASYNC", "1")
    # Reload monitoring so it sees the env (read at request time — already does)
    reg = client.post(
        "/api/v1/auth/register",
        json={"store_name": "Queue Store", "username": "q_owner", "password": "secret12"},
    )
    token = reg.json()["token"]
    r = client.post(
        "/api/v1/monitoring/refresh?skip_forecast=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["job_id"]
