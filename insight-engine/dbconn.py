"""
Shared DB access for the insight engine — same pattern as ml-models / dashboard.
"""
from __future__ import annotations

import os
import sys

PIPELINE_ROOT = os.environ.get(
    "INVOICE_PIPELINE_ROOT",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "invoice-pipeline",
    ),
)
if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

ML_ROOT = os.environ.get(
    "ML_MODELS_ROOT",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ml-models",
    ),
)
if ML_ROOT not in sys.path:
    sys.path.insert(0, ML_ROOT)

from db.session import get_session, engine, init_db  # noqa: E402
from db.models import Base, Store, Product, Insight  # noqa: E402


def ensure_ml_tables():
    """Register ML result models so collectors can query them."""
    import common.result_models  # noqa: F401


def resolve_store_id(session, store_id: str | None = None, store_name: str | None = None) -> str | None:
    if store_id:
        return store_id
    if not store_name:
        return None
    store = session.query(Store).filter(Store.store_name == store_name).first()
    if not store:
        raise ValueError(f"Store not found: {store_name}")
    return store.store_id


def load_product_names(session) -> dict[str, str]:
    return {p.product_id: p.product_name for p in session.query(Product).all()}
