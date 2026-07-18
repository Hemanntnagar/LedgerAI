"""
Step 5 orchestrator: run all ML modules for a store (or all stores).

Callable from CLI and from dashboard report generation.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger("ml.orchestrator")


def _ensure_path():
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)


def run_all_modules(
    store_id: Optional[str] = None,
    store_name: Optional[str] = None,
    horizon_days: Optional[int] = None,
    *,
    skip_forecast: bool = False,
) -> Dict[str, Any]:
    """
    Refresh every ML result table. When store_id/store_name is set, scopes
    per-store jobs to that store (global rows skipped where applicable).
    """
    _ensure_path()

    from common.data_loaders import get_session, resolve_store_id, Base, engine
    from common import result_models  # noqa: F401 — register tables
    from abc_classification.job import run_abc_classification_job
    from basket_analysis.job import run_basket_analysis_job
    from elasticity.job import run_elasticity_job
    from anomaly_detection.job import run_anomaly_detection_job
    from forecasting.job import run_forecast_job
    from customer_targeting.job import run_customer_targeting_job
    from inventory.job import run_inventory_job
    from timing.job import run_timing_job
    from store_benchmarking.job import run_benchmarking_job
    from festival.job import run_festival_job

    Base.metadata.create_all(bind=engine)

    resolved = store_id
    if store_name or not store_id:
        session = get_session()
        try:
            resolved = resolve_store_id(session, store_id=store_id, store_name=store_name)
        finally:
            session.close()

    results: Dict[str, Any] = {"store_id": resolved}

    steps = [
        ("abc", lambda: run_abc_classification_job(store_id=resolved)),
        ("basket", lambda: run_basket_analysis_job(store_id=resolved)),
        ("elasticity", lambda: run_elasticity_job(store_id=resolved)),
        ("anomaly", lambda: run_anomaly_detection_job(store_id=resolved)),
        ("inventory", lambda: run_inventory_job(store_id=resolved)),
        ("customer_targeting", lambda: run_customer_targeting_job(store_id=resolved)),
        ("timing", lambda: run_timing_job(store_id=resolved)),
        ("benchmarking", lambda: run_benchmarking_job(store_id=resolved)),
        ("festival", lambda: run_festival_job(store_id=resolved)),
    ]
    if not skip_forecast:
        steps.append(("forecast", lambda: run_forecast_job(horizon_days=horizon_days, store_id=resolved)))

    for name, fn in steps:
        logger.info("=== %s ===", name)
        try:
            results[name] = fn()
        except Exception as exc:
            logger.exception("%s failed: %s", name, exc)
            results[name] = {"error": str(exc)}

    return results
