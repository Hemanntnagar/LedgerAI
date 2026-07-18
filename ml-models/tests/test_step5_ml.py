"""
Step 5 ML expansion tests: MAPE confidence, RFM, festival stock/offer, inventory.
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

ML_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ML_ROOT)

PIPELINE_ROOT = os.environ.get(
    "INVOICE_PIPELINE_ROOT",
    os.path.join(os.path.dirname(ML_ROOT), "invoice-pipeline"),
)
sys.path.insert(0, PIPELINE_ROOT)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "step5.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", PIPELINE_ROOT)

    import importlib
    import db.session as pipeline_session
    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    # Re-bind ML loaders to the temp engine (do NOT reload result_models —
    # that re-registers tables on the shared Base metadata).
    import common.data_loaders as loaders
    importlib.reload(loaders)
    from common import result_models  # noqa: F401
    loaders.Base.metadata.create_all(bind=loaders.engine)

    from db.models import Store, Product, Customer, Invoice, InvoiceLineItem
    from db.seed_festivals import seed_festival_events
    from db.phone import normalize_phone

    session = pipeline_session.get_session()
    store = Store(store_name="Festive Kirana")
    session.add(store)
    session.flush()

    products = []
    for name, cat in [("Sweets Box", "Festive"), ("Basmati Rice", "Grocery"), ("Oil 1L", "Grocery")]:
        p = Product(product_name=name, category=cat)
        session.add(p)
        session.flush()
        products.append(p)

    phone = normalize_phone("9876543210")
    cust = Customer(phone=phone, phone_normalized=phone, customer_name="Ravi")
    session.add(cust)
    session.flush()

    # History covering Diwali 2025 through mid-2026 so festival lift is measurable
    start = date(2025, 9, 1)
    end = date(2026, 6, 1)
    d = start
    i = 0
    while d <= end:
        is_diwali = date(2025, 10, 18) <= d <= date(2025, 10, 23)
        mult = 3.0 if is_diwali else 1.0
        for j, p in enumerate(products):
            qty = (2 + j) * mult
            price = 100 + j * 20
            total = qty * price
            inv = Invoice(
                store_id=store.store_id,
                invoice_number=f"S5-{i}-{j}",
                invoice_date=d,
                grand_total=total,
                customer_id=cust.customer_id if j == 0 else None,
                source_type="csv",
                source_file_path="test",
            )
            session.add(inv)
            session.flush()
            session.add(InvoiceLineItem(
                invoice_id=inv.invoice_id,
                product_id=p.product_id,
                quantity=qty,
                unit_price=price,
                line_total=total,
            ))
        d += timedelta(days=1)
        i += 1

    # Second customer dormant (last purchase ~80 days before end)
    phone2 = normalize_phone("9123456789")
    cust2 = Customer(phone=phone2, phone_normalized=phone2, customer_name="Old")
    session.add(cust2)
    session.flush()
    old_d = end - timedelta(days=80)
    inv = Invoice(
        store_id=store.store_id,
        invoice_number="OLD-1",
        invoice_date=old_d,
        grand_total=500,
        customer_id=cust2.customer_id,
        source_type="csv",
        source_file_path="test",
    )
    session.add(inv)
    session.flush()
    session.add(InvoiceLineItem(
        invoice_id=inv.invoice_id,
        product_id=products[0].product_id,
        quantity=1,
        unit_price=500,
        line_total=500,
    ))

    seed_festival_events(session)
    session.commit()
    store_id = store.store_id
    product_ids = [p.product_id for p in products]
    session.close()

    return {"store_id": store_id, "product_ids": product_ids, "db_path": str(db_path)}


def test_confidence_from_mape():
    from forecasting.model import confidence_from_mape
    assert confidence_from_mape(10) == "high"
    assert confidence_from_mape(30) == "medium"
    assert confidence_from_mape(50) == "low"
    assert confidence_from_mape(None) == "low"


def test_prophet_confidence_uses_mape_not_always_high():
    from forecasting.model import forecast_product, confidence_from_mape
    # Explicit high MAPE must force low confidence even with long history
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    series = pd.DataFrame({"date": dates, "units": [10] * 60, "revenue": [1000] * 60})
    result = forecast_product(series, horizon_days=7, grain="daily", mape_pct=55.0, _skip_mape=True)
    assert (result["confidence_level"] == "low").all()
    assert confidence_from_mape(55.0) == "low"


def test_customer_rfm_and_dormant(seeded_db):
    from customer_targeting.job import run_customer_targeting_job
    from common.data_loaders import get_session
    from common.result_models import CustomerSegment

    summary = run_customer_targeting_job(store_id=seeded_db["store_id"])
    assert summary["segments"] >= 2
    assert summary["skipped_no_customers"] is False

    session = get_session()
    try:
        segs = session.query(CustomerSegment).filter_by(store_id=seeded_db["store_id"]).all()
        labels = {s.segment for s in segs}
        assert "dormant" in labels or any(s.recency_days >= 60 for s in segs)
    finally:
        session.close()


def test_festival_stock_up_suggestions(seeded_db):
    from festival.job import run_festival_job
    from common.data_loaders import get_session
    from common.result_models import FestivalInsight

    summary = run_festival_job(store_id=seeded_db["store_id"])
    assert summary["insights"] >= 1

    session = get_session()
    try:
        rows = session.query(FestivalInsight).filter_by(store_id=seeded_db["store_id"]).all()
        assert len(rows) >= 1
        actions = {r.action for r in rows}
        assert actions & {"stock_up", "offer", "watch"}
        assert any("stock" in (r.suggestion_text or "").lower() or "festival" in (r.suggestion_text or "").lower() for r in rows)
    finally:
        session.close()


def test_inventory_dead_stock_signal(seeded_db):
    """Product with no recent sales → dead_stock (inject gap product)."""
    from common.data_loaders import get_session
    from db.models import Product, Invoice, InvoiceLineItem
    from inventory.job import run_inventory_job
    from common.result_models import InventorySignal
    from datetime import date as d

    session = get_session()
    try:
        p = Product(product_name="Stale Item", category="Misc")
        session.add(p)
        session.flush()
        inv = Invoice(
            store_id=seeded_db["store_id"],
            invoice_number="STALE-1",
            invoice_date=d(2025, 1, 1),
            grand_total=10,
            source_type="csv",
            source_file_path="test",
        )
        session.add(inv)
        session.flush()
        session.add(InvoiceLineItem(
            invoice_id=inv.invoice_id,
            product_id=p.product_id,
            quantity=1,
            unit_price=10,
            line_total=10,
        ))
        session.commit()
        stale_id = p.product_id
    finally:
        session.close()

    summary = run_inventory_job(store_id=seeded_db["store_id"])
    assert summary["signals"] >= 1

    session = get_session()
    try:
        sigs = (
            session.query(InventorySignal)
            .filter_by(store_id=seeded_db["store_id"], product_id=stale_id, signal_type="dead_stock")
            .all()
        )
        assert len(sigs) == 1
        assert "dead stock" in sigs[0].suggestion_text.lower()
    finally:
        session.close()


def test_run_all_store_scoped(seeded_db):
    from orchestrator import run_all_modules

    results = run_all_modules(store_id=seeded_db["store_id"], skip_forecast=True)
    assert results["store_id"] == seeded_db["store_id"]
    for key in ("abc", "basket", "elasticity", "anomaly", "inventory", "festival", "customer_targeting"):
        assert key in results
        assert "error" not in results[key], results[key]
