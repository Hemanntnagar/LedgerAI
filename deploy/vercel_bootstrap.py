"""
Shared bootstrap for Vercel / serverless: paths, demo SQLite, feature flags.

Imported by api/index.py before dashboard.backend.main (so DB URL is set
before SQLAlchemy engine creation).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def is_serverless() -> bool:
    if os.environ.get("VERCEL") == "1":
        return True
    return os.environ.get("LEDGER_SERVERLESS", "").lower() in ("1", "true", "yes")


def apply_env() -> Path:
    """Set ROOT paths + serverless defaults. Returns repo root."""
    pipeline = REPO_ROOT / "invoice-pipeline"
    ml = REPO_ROOT / "ml-models"
    insight = REPO_ROOT / "insight-engine"
    backend = REPO_ROOT / "dashboard" / "backend"

    os.environ.setdefault("INVOICE_PIPELINE_ROOT", str(pipeline))
    os.environ.setdefault("ML_MODELS_ROOT", str(ml))
    os.environ.setdefault("INSIGHT_ENGINE_ROOT", str(insight))

    if is_serverless():
        os.environ.setdefault("SKIP_ML_ON_REPORT", "1")
        os.environ.setdefault("SKIP_INSIGHTS_ON_REPORT", "1")
        os.environ.setdefault("SKIP_INSIGHTS_ON_NIGHTLY", "1")
        os.environ.setdefault("ML_SKIP_FORECAST_ON_REPORT", "1")
        os.environ.setdefault("ENABLE_SCHEDULER", "0")
        os.environ.setdefault("LEDGER_SERVERLESS", "1")

    _ensure_database()

    # Backend must win over insight-engine (both have dbconn.py).
    ordered = [str(insight), str(ml), str(pipeline), str(backend)]
    for path in ordered:
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)

    return REPO_ROOT


def _ensure_database() -> None:
    """
    Prefer INVOICE_DB_URL (Postgres or SQLite).
    On Vercel without a URL, copy the bundled demo DB into /tmp (writable).
    """
    if os.environ.get("INVOICE_DB_URL"):
        return

    bundled = REPO_ROOT / "deploy" / "demo_ledger.db"
    # Local non-Vercel: leave default relative sqlite for invoice-pipeline CLI.
    if not is_serverless():
        return

    dest = Path(os.environ.get("LEDGER_TMP_DB", "/tmp/ledger_demo.db"))
    if bundled.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Always refresh from the bundled snapshot so deploys pick up new seeds.
        shutil.copy2(bundled, dest)
        os.environ["INVOICE_DB_URL"] = f"sqlite:///{dest}"
        return

    # Last resort: empty DB path (tables created on first request if needed)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.environ["INVOICE_DB_URL"] = f"sqlite:///{dest}"


def attach_serverless_guards(app) -> None:
    """Block long-running ML / refresh routes on serverless demos."""
    if not is_serverless():
        return

    from fastapi import Request
    from fastapi.responses import JSONResponse

    blocked = {
        ("POST", "/api/v1/reports"),
        ("POST", "/api/v1/monitoring/refresh"),
        ("POST", "/api/v1/pilot/backtest"),
        ("POST", "/api/v1/pilot/tune"),
    }

    @app.middleware("http")
    async def _block_heavy(request: Request, call_next):
        path = request.url.path
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]
        if (request.method.upper(), path) in blocked:
            return JSONResponse(
                status_code=501,
                content={
                    "detail": (
                        "This action is disabled on the Vercel serverless demo "
                        "(ML refresh / report generation exceeds function limits). "
                        "Browse Action Center, Revenue, and Intelligence on the "
                        "pre-seeded store instead."
                    )
                },
            )
        return await call_next(request)

    @app.get("/api/health")
    def api_health():
        return {"status": "ok", "serverless": True}
