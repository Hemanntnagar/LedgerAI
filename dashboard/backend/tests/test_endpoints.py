"""
Tests for the dashboard API. Uses an isolated temp SQLite DB seeded with
known synthetic data so assertions are exact, not just 'did it not crash'.
"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_app(monkeypatch, tmp_path):
    db_path = tmp_path / "test_dashboard.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", os.environ.get(
        "INVOICE_PIPELINE_ROOT",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "invoice-pipeline"),
    ))

    pipeline_root = os.environ["INVOICE_PIPELINE_ROOT"]
    if pipeline_root not in sys.path:
        sys.path.insert(0, pipeline_root)

    import importlib

    # Reload in dependency order: db.session picks up the new env var,
    # then dbconn (which imports from db.session), then queries/main.
    import db.session as pipeline_session
    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    import dbconn
    importlib.reload(dbconn)

    # Seed known data directly via the pipeline's models
    from db.models import Store, Product, Invoice, InvoiceLineItem
    session = pipeline_session.get_session()

    store = Store(store_name="Test Store")
    session.add(store)
    session.flush()

    product = Product(product_name="Test Widget", category="TestCat")
    session.add(product)
    session.flush()

    inv1 = Invoice(
        store_id=store.store_id, invoice_number="T-1", invoice_date=date(2026, 6, 1),
        grand_total=100.0, source_type="csv", source_file_path="test",
    )
    session.add(inv1)
    session.flush()
    session.add(InvoiceLineItem(invoice_id=inv1.invoice_id, product_id=product.product_id,
                                 quantity=2, unit_price=50, line_total=100.0))

    inv2 = Invoice(
        store_id=store.store_id, invoice_number="T-2", invoice_date=date(2026, 6, 2),
        grand_total=200.0, source_type="csv", source_file_path="test",
    )
    session.add(inv2)
    session.flush()
    session.add(InvoiceLineItem(invoice_id=inv2.invoice_id, product_id=product.product_id,
                                 quantity=4, unit_price=50, line_total=200.0))

    session.commit()
    session.close()

    import queries
    importlib.reload(queries)
    import cache as cache_module
    importlib.reload(cache_module)
    import main
    importlib.reload(main)
    return main.app


def test_kpis_returns_correct_totals(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/kpis?start_date=2026-06-01&end_date=2026-06-02")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_revenue"] == 300.0
    assert data["total_transactions"] == 2
    assert data["avg_basket_value"] == 150.0
    assert data["units_sold"] == 6.0


def test_kpis_rejects_bad_date_range(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/kpis?start_date=2026-06-02&end_date=2026-06-01")
    assert resp.status_code == 400


def test_kpis_rejects_unknown_store(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/kpis?start_date=2026-06-01&end_date=2026-06-02&store_id=nonexistent")
    assert resp.status_code == 400


def test_top_products_returns_expected_product(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/top-products?start_date=2026-06-01&end_date=2026-06-02")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["product_name"] == "Test Widget"
    assert data[0]["revenue"] == 300.0
    assert data[0]["pct_of_total_revenue"] == 100.0


def test_pareto_cutoff_with_single_product(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/pareto?start_date=2026-06-01&end_date=2026-06-02")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["products_making_80pct_revenue"] == 1
    assert data["products"][0]["cumulative_pct"] == 100.0


def test_category_breakdown(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/category-breakdown?start_date=2026-06-01&end_date=2026-06-02")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["category"] == "TestCat"
    assert data[0]["revenue"] == 300.0


def test_stores_endpoint_lists_seeded_store(test_app):
    client = TestClient(test_app)
    resp = client.get("/api/stores")
    assert resp.status_code == 200
    names = [s["store_name"] for s in resp.json()["data"]]
    assert "Test Store" in names


def test_health_endpoint(test_app):
    client = TestClient(test_app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
