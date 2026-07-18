"""
Step 3 — revenue facts rollup matches live invoice SQL.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def session(tmp_path, monkeypatch):
    db_path = tmp_path / "step3.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")

    import importlib
    import db.session as sess_mod
    importlib.reload(sess_mod)
    sess_mod.init_db()

    from db.models import Store, Product, Invoice, InvoiceLineItem

    s = sess_mod.get_session()
    store = Store(store_name="Revenue Store")
    s.add(store)
    s.flush()

    rice = Product(product_name="Basmati Rice", category="Grains")
    oil = Product(product_name="Mustard Oil", category="Oils")
    s.add_all([rice, oil])
    s.flush()

    # June: 3 days across two ISO weeks
    samples = [
        (date(2026, 6, 1), rice, 100.0, 2),   # Mon week 23
        (date(2026, 6, 2), oil, 50.0, 1),     # Tue week 23
        (date(2026, 6, 8), rice, 200.0, 4),   # Mon week 24
        (date(2026, 7, 1), oil, 80.0, 2),     # July
    ]
    for i, (d, prod, total, qty) in enumerate(samples):
        inv = Invoice(
            store_id=store.store_id,
            invoice_number=f"R-{i}",
            invoice_date=d,
            grand_total=total,
            source_type="csv",
            source_file_path="test",
        )
        s.add(inv)
        s.flush()
        s.add(
            InvoiceLineItem(
                invoice_id=inv.invoice_id,
                product_id=prod.product_id,
                quantity=qty,
                unit_price=total / qty,
                line_total=total,
            )
        )
    s.commit()
    yield s, store.store_id
    s.close()


def test_daily_facts_match_invoice_sums(session):
    from db.revenue_facts import rollup_revenue_facts
    from db.models import DailyRevenueFact
    from sqlalchemy import func
    from db.models import Invoice

    sess, store_id = session
    result = rollup_revenue_facts(sess, store_id=store_id)
    sess.commit()
    assert result["daily_rows"] == 4

    facts = {
        f.fact_date: float(f.revenue)
        for f in sess.query(DailyRevenueFact).filter_by(store_id=store_id).all()
    }
    live = dict(
        sess.query(Invoice.invoice_date, func.sum(Invoice.grand_total))
        .filter_by(store_id=store_id)
        .group_by(Invoice.invoice_date)
        .all()
    )
    for d, rev in live.items():
        assert facts[d] == float(rev)


def test_monthly_facts_match_and_include_breakdowns(session):
    from db.revenue_facts import rollup_revenue_facts
    from db.models import MonthlyRevenueFact
    from sqlalchemy import func
    from db.models import Invoice

    sess, store_id = session
    rollup_revenue_facts(sess, store_id=store_id)
    sess.commit()

    june = (
        sess.query(MonthlyRevenueFact)
        .filter_by(store_id=store_id, year=2026, month=6)
        .one()
    )
    live_june = (
        sess.query(func.coalesce(func.sum(Invoice.grand_total), 0))
        .filter(
            Invoice.store_id == store_id,
            Invoice.invoice_date.between(date(2026, 6, 1), date(2026, 6, 30)),
        )
        .scalar()
    )
    assert float(june.revenue) == float(live_june)
    assert june.transactions == 3
    assert float(june.units_sold) == 7.0
    cats = {c["category"]: c["revenue"] for c in june.by_category_json}
    assert cats["Grains"] == 300.0
    assert cats["Oils"] == 50.0
    assert any(p["product_name"] == "Basmati Rice" for p in june.by_product_json)


def test_weekly_aggregate_from_daily_matches_live(session):
    from db.revenue_facts import rollup_revenue_facts, aggregate_weekly_from_daily

    sess, store_id = session
    rollup_revenue_facts(sess, store_id=store_id)
    sess.commit()

    weeks = aggregate_weekly_from_daily(
        sess,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        store_id=store_id,
    )
    by_period = {w["period"]: w for w in weeks}
    # 2026-06-01 is ISO week 23
    assert by_period["2026-W23"]["revenue"] == 150.0
    assert by_period["2026-W23"]["transactions"] == 2
    assert by_period["2026-W24"]["revenue"] == 200.0
