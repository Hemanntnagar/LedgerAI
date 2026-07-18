"""
Step 6 — Insight Engine: deterministic ranking from frozen ML fixtures.
Zero network / LLM calls.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
PIPELINE = REPO / "invoice-pipeline"
ML = REPO / "ml-models"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(ML))


@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    db_path = tmp_path / "step6.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", str(PIPELINE))
    monkeypatch.setenv("ML_MODELS_ROOT", str(ML))

    # Clear cached rules config between tests
    import config_loader
    config_loader.load_rules_config.cache_clear()

    import importlib
    import db.session as pipeline_session
    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    # Register ML tables
    from common import result_models  # noqa: F401
    from common.data_loaders import Base, engine
    Base.metadata.create_all(bind=engine)

    from db.models import Store, Product
    from common.result_models import (
        AssociationRule,
        PriceElasticity,
        InventorySignal,
        CustomerSegment,
        FestivalInsight,
        Anomaly,
        ABCClassification,
        ForecastResult,
    )
    from db.models import Insight

    session = pipeline_session.get_session()
    store = Store(store_name="Insight Test Store")
    session.add(store)
    session.flush()

    rice = Product(product_name="Rice 5kg", category="Staples")
    oil = Product(product_name="Sunflower Oil", category="Staples")
    sweets = Product(product_name="Mithai Box", category="Festival")
    dead = Product(product_name="Old Soap", category="Personal")
    session.add_all([rice, oil, sweets, dead])
    session.flush()

    # Bundle: strong rule
    session.add(AssociationRule(
        store_id=store.store_id,
        antecedent_product_ids=[rice.product_id],
        consequent_product_ids=[oil.product_id],
        support=0.05,
        confidence=0.72,
        lift=2.1,
        suggestion_text="72% of customers who buy Rice 5kg also buy Sunflower Oil",
    ))
    # Weak rule — should be filtered by min_lift / confidence
    session.add(AssociationRule(
        store_id=store.store_id,
        antecedent_product_ids=[oil.product_id],
        consequent_product_ids=[dead.product_id],
        support=0.01,
        confidence=0.2,
        lift=1.05,
    ))

    # Promo floor elastic SKU → stop_discount
    session.add(PriceElasticity(
        store_id=store.store_id,
        product_id=oil.product_id,
        elasticity_coefficient=-1.8,
        r_squared=0.65,
        classification="elastic",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        promo_floor_flag=True,
        margin_safe=False,
    ))

    # Inventory signals
    session.add(InventorySignal(
        store_id=store.store_id,
        product_id=sweets.product_id,
        signal_type="velocity_surge",
        days_since_sale=0,
        recent_units_per_day=12.0,
        baseline_units_per_day=4.0,
        severity="high",
    ))
    session.add(InventorySignal(
        store_id=store.store_id,
        product_id=dead.product_id,
        signal_type="dead_stock",
        days_since_sale=60,
        recent_units_per_day=0.0,
        baseline_units_per_day=0.5,
        severity="high",
    ))

    # Customers for win-back / VIP
    for i, seg in enumerate(["dormant", "dormant", "at_risk", "vip", "vip", "regular"]):
        session.add(CustomerSegment(
            store_id=store.store_id,
            customer_id=f"cust-{i}",
            phone_normalized=f"+9198765432{i:02d}",
            recency_days=80 if seg != "vip" else 5,
            frequency=3,
            monetary=5000 if seg == "vip" else 800,
            r_score=1,
            f_score=3,
            m_score=3,
            segment=seg,
            churn_score=0.8 if seg == "dormant" else 0.2,
        ))

    session.add(FestivalInsight(
        store_id=store.store_id,
        festival_name="Diwali",
        festival_year=2026,
        window_start=date(2026, 10, 20),
        window_end=date(2026, 11, 5),
        product_id=sweets.product_id,
        lift_vs_baseline=1.8,
        action="stock_up",
        suggestion_text="For Diwali: stock up on Mithai Box",
    ))

    session.add(Anomaly(
        store_id=store.store_id,
        product_id=rice.product_id,
        anomaly_date=date(2026, 7, 1),
        metric="revenue",
        actual_value=50,
        expected_value=500,
        z_score=-3.2,
        severity="high",
    ))

    session.add(ABCClassification(
        store_id=store.store_id,
        product_id=rice.product_id,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        revenue_rank=1,
        cumulative_pct=0.35,
        product_class="A",
    ))

    session.add(ForecastResult(
        store_id=store.store_id,
        product_id=sweets.product_id,
        forecast_date=date(2026, 7, 20),
        predicted_units=80,
        confidence_level="medium",
        backtest_mape_pct=30.0,
        grain="weekly",
        model_version="test",
    ))

    session.commit()
    out = {
        "store_id": store.store_id,
        "db_path": str(db_path),
        "rice_id": rice.product_id,
        "oil_id": oil.product_id,
        "sweets_id": sweets.product_id,
        "dead_id": dead.product_id,
    }
    session.close()
    return out


def test_deterministic_ranking(engine_env):
    from engine import run_insight_engine

    a = run_insight_engine(store_id=engine_env["store_id"], persist=True)
    b = run_insight_engine(store_id=engine_env["store_id"], persist=True)

    assert a["store_id"] == engine_env["store_id"]
    assert a["after_conflicts"] > 0
    assert len(a["actions"]) == len(b["actions"])
    assert [x["action_type"] for x in a["actions"]] == [x["action_type"] for x in b["actions"]]
    assert [x["score"] for x in a["actions"]] == [x["score"] for x in b["actions"]]
    assert [x["title"] for x in a["actions"]] == [x["title"] for x in b["actions"]]

    # Scores must be non-increasing
    scores = [x["score"] for x in a["actions"]]
    assert scores == sorted(scores, reverse=True)


def test_expected_action_types_present(engine_env):
    from engine import run_insight_engine

    result = run_insight_engine(store_id=engine_env["store_id"], persist=True)
    types = {a["action_type"] for a in result["actions"]}
    assert "bundle" in types
    assert "stop_discount" in types
    assert "reorder" in types
    assert "clear_dead_stock" in types
    assert "win_back" in types
    assert "vip_nurture" in types
    assert "festival_prep" in types
    assert "anomaly_review" in types


def test_weak_bundle_filtered(engine_env):
    from engine import run_insight_engine

    result = run_insight_engine(store_id=engine_env["store_id"], persist=False)
    bundles = [a for a in result["actions"] if a["action_type"] == "bundle"]
    assert len(bundles) == 1
    assert "Rice" in bundles[0]["title"]
    assert "Oil" in bundles[0]["title"]


def test_conflict_reorder_vs_dead_stock_same_product(engine_env, monkeypatch, tmp_path):
    """If both reorder and clear_dead_stock fire for same SKU, keep higher score."""
    import importlib
    import db.session as pipeline_session
    from common.result_models import InventorySignal

    session = pipeline_session.get_session()
    # Add conflicting surge on the dead_stock product
    session.add(InventorySignal(
        store_id=engine_env["store_id"],
        product_id=engine_env["dead_id"],
        signal_type="velocity_surge",
        days_since_sale=1,
        recent_units_per_day=20.0,
        baseline_units_per_day=2.0,
        severity="high",
    ))
    session.commit()
    session.close()

    from engine import run_insight_engine
    from dedupe import resolve_conflicts
    from config_loader import load_rules_config

    result = run_insight_engine(store_id=engine_env["store_id"], persist=False)
    dead_actions = [
        a for a in result["actions"]
        if engine_env["dead_id"] in (a.get("product_ids") or [])
        and a["action_type"] in ("reorder", "clear_dead_stock")
    ]
    # Mutex should leave at most one of the pair for that product
    types = {a["action_type"] for a in dead_actions}
    assert not ({"reorder", "clear_dead_stock"} <= types)


def test_persists_to_insights_table(engine_env):
    from engine import run_insight_engine
    import db.session as pipeline_session
    from db.models import Insight

    run_insight_engine(store_id=engine_env["store_id"], persist=True)
    session = pipeline_session.get_session()
    rows = (
        session.query(Insight)
        .filter_by(store_id=engine_env["store_id"], status="new")
        .order_by(Insight.score.desc())
        .all()
    )
    assert len(rows) > 0
    assert rows[0].title
    assert rows[0].body
    assert rows[0].score is not None
    assert rows[0].source_models
    # Second run replaces new insights (count stable-ish, all still new)
    count1 = len(rows)
    session.close()

    run_insight_engine(store_id=engine_env["store_id"], persist=True)
    session = pipeline_session.get_session()
    count2 = session.query(Insight).filter_by(
        store_id=engine_env["store_id"], status="new"
    ).count()
    session.close()
    assert count2 == count1


def test_keeps_accepted_insights(engine_env):
    from engine import run_insight_engine
    import db.session as pipeline_session
    from db.models import Insight

    run_insight_engine(store_id=engine_env["store_id"], persist=True)
    session = pipeline_session.get_session()
    first = session.query(Insight).filter_by(store_id=engine_env["store_id"]).first()
    first.status = "accepted"
    accepted_id = first.insight_id
    session.commit()
    session.close()

    run_insight_engine(store_id=engine_env["store_id"], persist=True)
    session = pipeline_session.get_session()
    still = session.query(Insight).filter_by(insight_id=accepted_id).one()
    assert still.status == "accepted"
    session.close()


def test_no_llm_imports():
    """Guardrail: insight-engine must not depend on Anthropic/OpenAI."""
    root = ROOT
    banned = ("anthropic", "openai", "httpx", "requests")
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name.startswith("."):
            continue
        text = path.read_text()
        for b in banned:
            assert f"import {b}" not in text, f"{path} imports {b}"
            assert f"from {b}" not in text, f"{path} imports {b}"
