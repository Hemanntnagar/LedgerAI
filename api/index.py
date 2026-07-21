"""
Vercel FastAPI entrypoint for Ledger.

Vercel looks for a top-level `app` in api/index.py.
Static UI is served from public/ (copied from dashboard/frontend at build).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from deploy.vercel_bootstrap import apply_env, attach_serverless_guards

apply_env()

from main import app  # noqa: E402  — dashboard/backend on sys.path

try:
    from ml_path import load_result_models

    load_result_models()
except Exception:
    pass

attach_serverless_guards(app)
