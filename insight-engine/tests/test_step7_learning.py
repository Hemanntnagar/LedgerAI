"""Step 7 unit tests for feedback → weight learning (insight-engine)."""
from __future__ import annotations

import os
import sys
from datetime import date
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
def learning_env(monkeypatch, tmp_path):
    db_path = tmp_path / "step7_ie.db"
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("INVOICE_PIPELINE_ROOT", str(PIPELINE))
    monkeypatch.setenv("ML_MODELS_ROOT", str(ML))

    import config_loader
    config_loader.load_rules_config.cache_clear()

    import importlib
    import db.session as pipeline_session
    importlib.reload(pipeline_session)
    pipeline_session.init_db()

    from common import result_models  # noqa: F401
    from common.data_loaders import Base, engine
    Base.metadata.create_all(bind=engine)

    from db.models import Store, Insight
    from common.result_models import AssociationRule
    from db.models import Product

    session = pipeline_session.get_session()
    store = Store(store_name="Learn Store")
    session.add(store)
    session.flush()

    rice = Product(product_name="Rice", category="Staples")
    oil = Product(product_name="Oil", category="Staples")
    session.add_all([rice, oil])
    session.flush()

    session.add(
        AssociationRule(
            store_id=store.store_id,
            antecedent_product_ids=[rice.product_id],
            consequent_product_ids=[oil.product_id],
            support=0.05,
            confidence=0.8,
            lift=2.5,
            suggestion_text="bundle rice+oil",
        )
    )

    # Heavy useful feedback on bundle → weight up
    for _ in range(6):
        session.add(
            Insight(
                store_id=store.store_id,
                action_type="bundle",
                priority="high",
                title="old",
                body="old",
                score=1,
                feedback="useful",
                status="accepted",
                period_start=date(2026, 5, 1),
                period_end=date(2026, 5, 31),
            )
        )
    session.commit()

    yield session, store
    session.close()


def test_feedback_raises_bundle_weight(learning_env):
    session, store = learning_env
    from config_loader import load_rules_config
    from learning import learned_weight_multipliers, weight_multiplier

    cfg = load_rules_config()
    mults = learned_weight_multipliers(session, store.store_id, cfg)
    assert "bundle" in mults
    assert mults["bundle"] > 1.0
    assert mults["bundle"] == weight_multiplier(6, 0)


def test_engine_applies_learned_weights(learning_env):
    session, store = learning_env
    from engine import run_insight_engine

    # Without feedback path already tested in step 6; here multipliers appear
    summary = run_insight_engine(
        store_id=store.store_id,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        persist=True,
        session=session,
    )
    session.commit()
    assert summary["learned_weight_multipliers"].get("bundle", 1.0) > 1.0
    assert summary["persisted"] >= 1
    # Evidence should carry the multiplier on scored actions
    bundles = [a for a in summary["actions"] if a["action_type"] == "bundle"]
    assert bundles
    assert bundles[0]["evidence"].get("learned_weight_multiplier", 1) > 1.0
