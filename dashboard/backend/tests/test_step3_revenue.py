"""
Step 3 — /api/revenue day/week/month matches live SQL and facts after rollup.
"""
import os
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_app(monkeypatch, tmp_path):
    db_path = tmp_path / "test_revenue.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    monkeypatch.setenv(
        "INVOICE_PIPELINE_ROOT",
        os.environ.get("INVOICE_PIPELINE_ROOT", os.path.join(_repo, "invoice-pipeline")),
    )

    pipeline_root = os.environ["INVOICE_PIPELINE_ROOT"]
    if pipeline_root not in sys.path:
        sys.path.insert(0, pipeline_root)

    import importlib
    import db.session as pipeline_session
    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    import dbconn
    importlib.reload(dbconn)

    from db.models import Store, Product, Invoice, InvoiceLineItem

    session = pipeline_session.get_session()
    store = Store(store_name="Rev Store")
    session.add(store)
    session.flush()

    product = Product(product_name="Widget", category="Tools")
    session.add(product)
    session.flush()

    for i, (d, total, qty) in enumerate([
        (date(2026, 6, 1), 100.0, 2),
        (date(2026, 6, 2), 200.0, 4),
        (date(2026, 6, 8), 50.0, 1),
        (date(2026, 7, 5), 75.0, 3),
    ]):
        inv = Invoice(
            store_id=store.store_id,
            invoice_number=f"V-{i}",
            invoice_date=d,
            grand_total=total,
            source_type="csv",
            source_file_path="test",
        )
        session.add(inv)
        session.flush()
        session.add(
            InvoiceLineItem(
                invoice_id=inv.invoice_id,
                product_id=product.product_id,
                quantity=qty,
                unit_price=total / qty,
                line_total=total,
            )
        )
    session.commit()
    store_id = store.store_id
    session.close()

    import queries
    importlib.reload(queries)
    import cache as cache_module
    importlib.reload(cache_module)
    import main
    importlib.reload(main)

    return main.app, store_id, pipeline_session


def test_revenue_day_live_totals(test_app):
    app, store_id, _ = test_app
    client = TestClient(app)
    resp = client.get(
        f"/api/revenue?start_date=2026-06-01&end_date=2026-06-30&store_id={store_id}&granularity=day&prefer_facts=false"
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["granularity"] == "day"
    assert body["source"] == "live"
    assert body["totals"]["revenue"] == 350.0
    assert body["totals"]["transactions"] == 3
    assert body["totals"]["units_sold"] == 7.0
    assert len(body["series"]) == 3


def test_revenue_week_and_month(test_app):
    app, store_id, _ = test_app
    client = TestClient(app)

    week = client.get(
        f"/api/revenue?start_date=2026-06-01&end_date=2026-06-30&store_id={store_id}&granularity=week&prefer_facts=false"
    ).json()["data"]
    by_w = {r["period"]: r["revenue"] for r in week["series"]}
    assert by_w["2026-W23"] == 300.0
    assert by_w["2026-W24"] == 50.0

    month = client.get(
        f"/api/revenue?start_date=2026-06-01&end_date=2026-07-31&store_id={store_id}&granularity=month&prefer_facts=false"
    ).json()["data"]
    by_m = {r["period"]: r["revenue"] for r in month["series"]}
    assert by_m["2026-06"] == 350.0
    assert by_m["2026-07"] == 75.0


def test_facts_match_live_after_rollup(test_app):
    app, store_id, pipeline_session = test_app
    from db.revenue_facts import rollup_revenue_facts

    sess = pipeline_session.get_session()
    try:
        rollup_revenue_facts(sess, store_id=store_id)
        sess.commit()
    finally:
        sess.close()

    # Clear cache so prefer_facts hits DB
    import cache as cache_module
    cache_module.cache.clear()

    client = TestClient(app)
    for g in ("day", "week", "month"):
        live = client.get(
            f"/api/revenue?start_date=2026-06-01&end_date=2026-07-31&store_id={store_id}"
            f"&granularity={g}&prefer_facts=false"
        ).json()["data"]
        facts = client.get(
            f"/api/revenue?start_date=2026-06-01&end_date=2026-07-31&store_id={store_id}"
            f"&granularity={g}&prefer_facts=true"
        ).json()["data"]
        assert facts["source"] == "facts"
        assert facts["totals"]["revenue"] == live["totals"]["revenue"]
        assert facts["totals"]["transactions"] == live["totals"]["transactions"]
        live_map = {r["period"]: r["revenue"] for r in live["series"]}
        facts_map = {r["period"]: r["revenue"] for r in facts["series"]}
        assert live_map == facts_map


def test_revenue_rejects_bad_granularity(test_app):
    app, _, _ = test_app
    client = TestClient(app)
    resp = client.get("/api/revenue?start_date=2026-06-01&end_date=2026-06-30&granularity=year")
    assert resp.status_code == 400
