"""Ensure ml-models is importable so dashboard can read result tables."""
from __future__ import annotations

import os
import sys

_ML_ROOT = os.environ.get(
    "ML_MODELS_ROOT",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "ml-models",
    ),
)


def ensure_ml_on_path() -> str:
    if _ML_ROOT not in sys.path:
        sys.path.insert(0, _ML_ROOT)
    return _ML_ROOT


def load_result_models():
    """Import ML result ORM classes (registers tables on shared Base)."""
    ensure_ml_on_path()
    import common.result_models as rm  # noqa: F401

    return rm
