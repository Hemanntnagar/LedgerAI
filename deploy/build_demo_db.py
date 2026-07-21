"""
Build deploy/demo_ledger.db — self-contained SQLite demo for Vercel.

Usage (from repo root):
    python deploy/build_demo_db.py

Demo login: owner / demo1234  (store: Corner Kirana, plan: pro)
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "demo_ledger.db"
CSV = ROOT / "invoice-pipeline" / "tests" / "fixtures" / "sample_invoices.csv"

DEMO_USER = "owner"
DEMO_PASS = "demo1234"
DEMO_STORE = "Corner Kirana"


def main() -> None:
    if OUT.exists():
        OUT.unlink()

    db_url = f"sqlite:///{OUT}"
    os.environ["INVOICE_DB_URL"] = db_url
    os.environ["INVOICE_PIPELINE_ROOT"] = str(ROOT / "invoice-pipeline")
    os.environ["ML_MODELS_ROOT"] = str(ROOT / "ml-models")
    os.environ["INSIGHT_ENGINE_ROOT"] = str(ROOT / "insight-engine")

    pipeline = str(ROOT / "invoice-pipeline")
    ml = str(ROOT / "ml-models")
    for p in (pipeline, ml):
        if p not in sys.path:
            sys.path.insert(0, p)

    import importlib
    import db.session as pipeline_session

    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    from common import result_models  # noqa: F401
    from db.models import Base, Insight, Product, Invoice
    from db.api_keys import issue_api_key
    from db.store_auth import set_store_password
    from db.plans import PLAN_PRO, set_store_plan, seed_subscription_plans
    from db.seed_festivals import seed_festival_events
    from db.revenue_facts import rollup_revenue_facts
    from pipeline.ingest import ingest_file
    from common.result_models import (
        AssociationRule,
        CustomerSegment,
        FestivalInsight,
        ForecastResult,
        InventorySignal,
        PriceElasticity,
    )

    Base.metadata.create_all(bind=pipeline_session.engine)

    session = pipeline_session.get_session()
    try:
        seed_subscription_plans(session)
        session.commit()
    finally:
        session.close()

    seed_festival_events()

    if CSV.is_file():
        ingest_file(str(CSV), source_type="csv")

    session = pipeline_session.get_session()
    try:
        issued = issue_api_key(session, store_name=DEMO_STORE, plan="pro")
        set_store_password(
            session,
            store_id=issued.store_id,
            username=DEMO_USER,
            password=DEMO_PASS,
        )
        set_store_plan(session, issued.store_id, PLAN_PRO)
        store_id = issued.store_id

        product = session.query(Product).first()
        if not product:
            product = Product(product_name="Basmati Rice 5kg", category="Staples")
            session.add(product)
            session.flush()

        oil = (
            session.query(Product)
            .filter(Product.product_name.ilike("%oil%"))
            .first()
        )
        if not oil:
            oil = Product(product_name="Cooking Oil 1L", category="Staples")
            session.add(oil)
            session.flush()

        from db.models import InvoiceLineItem

        today = date.today()
        for i in range(14):
            day = today - timedelta(days=13 - i)
            qty_rice = 1 + (i % 3)
            qty_oil = 1 + (i % 2)
            rice_total = qty_rice * 450
            oil_total = qty_oil * 180
            grand = rice_total + oil_total
            inv = Invoice(
                store_id=store_id,
                invoice_number=f"DEMO-{1000 + i}",
                invoice_date=day,
                payment_method="UPI" if i % 2 == 0 else "Cash",
                subtotal=grand,
                tax_total=0,
                discount_total=0,
                grand_total=grand,
                source_type="api",
                source_file_path="deploy/build_demo_db.py",
            )
            session.add(inv)
            session.flush()
            session.add(
                InvoiceLineItem(
                    invoice_id=inv.invoice_id,
                    product_id=product.product_id,
                    quantity=qty_rice,
                    unit_price=450,
                    line_total=rice_total,
                )
            )
            session.add(
                InvoiceLineItem(
                    invoice_id=inv.invoice_id,
                    product_id=oil.product_id,
                    quantity=qty_oil,
                    unit_price=180,
                    line_total=oil_total,
                )
            )

        session.add(
            ForecastResult(
                store_id=store_id,
                product_id=product.product_id,
                forecast_date=today + timedelta(days=7),
                predicted_units=18,
                predicted_revenue=8100,
                confidence_level="medium",
                backtest_mape_pct=22.0,
                grain="weekly",
                model_version="demo",
            )
        )
        session.add(
            AssociationRule(
                store_id=store_id,
                antecedent_product_ids=[product.product_id],
                consequent_product_ids=[product.product_id],
                support=0.12,
                confidence=0.45,
                lift=2.2,
                suggestion_text="Bundle rice with cooking oil at checkout",
            )
        )
        session.add(
            PriceElasticity(
                store_id=store_id,
                product_id=product.product_id,
                elasticity_coefficient=-1.1,
                r_squared=0.48,
                classification="elastic",
                period_start=today - timedelta(days=60),
                period_end=today,
                promo_floor_flag=False,
                suggestion_text="Hold price — promo floor not needed",
            )
        )
        session.add(
            InventorySignal(
                store_id=store_id,
                product_id=product.product_id,
                signal_type="velocity_surge",
                days_since_sale=1,
                recent_units_per_day=1.8,
                baseline_units_per_day=1.0,
                severity="high",
                suggestion_text="Reorder rice before weekend rush",
            )
        )
        session.add(
            CustomerSegment(
                store_id=store_id,
                customer_id="demo-vip-1",
                phone_normalized="919876543210",
                recency_days=5,
                frequency=8,
                monetary=1200,
                r_score=5,
                f_score=4,
                m_score=4,
                segment="vip",
                churn_score=0.1,
            )
        )
        session.add(
            FestivalInsight(
                store_id=store_id,
                festival_name="Diwali",
                festival_year=today.year,
                window_start=today + timedelta(days=30),
                window_end=today + timedelta(days=45),
                product_id=product.product_id,
                lift_vs_baseline=1.6,
                action="stock_up",
                suggestion_text="Stock up Basmati 2 weeks before Diwali",
            )
        )
        session.add(
            Insight(
                store_id=store_id,
                action_type="bundle",
                priority="high",
                title="Bundle rice + oil at checkout",
                body=(
                    "Baskets with Basmati Rice often include Cooking Oil "
                    "(lift ~2.2). Suggest a combo near the till."
                ),
                score=82,
                impact_estimate=4500,
                confidence=0.75,
                urgency=0.6,
                status="new",
                source_models=["association_rules"],
            )
        )
        session.add(
            Insight(
                store_id=store_id,
                action_type="reorder",
                priority="high",
                title="Reorder Basmati Rice 5kg",
                body="Velocity surge detected — restock before the weekend.",
                score=78,
                impact_estimate=6000,
                confidence=0.7,
                urgency=0.85,
                status="new",
                source_models=["inventory"],
            )
        )
        session.add(
            Insight(
                store_id=store_id,
                action_type="festival_prep",
                priority="medium",
                title="Diwali: stock up Basmati Rice 5kg",
                body="Prep window open — historical lift ~1.6× vs baseline.",
                score=74,
                impact_estimate=12000,
                confidence=0.65,
                urgency=0.9,
                status="new",
                source_models=["festival_insights"],
            )
        )
        session.commit()
    finally:
        session.close()

    session = pipeline_session.get_session()
    try:
        rollup_revenue_facts(session)
        session.commit()
    except Exception as exc:
        session.rollback()
        print(f"rollup warning: {exc}")
    finally:
        session.close()

    session = pipeline_session.get_session()
    try:
        n_inv = session.query(Invoice).count()
    finally:
        session.close()

    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"Invoices: {n_inv}")
    print(f"Demo login → store={DEMO_STORE!r} user={DEMO_USER!r} password={DEMO_PASS!r}")


if __name__ == "__main__":
    main()
