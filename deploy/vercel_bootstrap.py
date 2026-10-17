"""
Shared bootstrap for Vercel / serverless: paths, demo SQLite, feature flags.

Imported by api/index.py before dashboard.backend.main (so DB URL is set
before SQLAlchemy engine creation).

Zero-config: no Vercel env vars required. Bundled deploy/demo_ledger.db
is copied to /tmp on every cold start.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def is_serverless() -> bool:
    # Vercel sets VERCEL=1; also accept common serverless markers.
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        return True
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
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
        os.environ["LEDGER_SERVERLESS"] = "1"
        os.environ.setdefault("SKIP_ML_ON_REPORT", "1")
        os.environ.setdefault("SKIP_INSIGHTS_ON_REPORT", "1")
        os.environ.setdefault("SKIP_INSIGHTS_ON_NIGHTLY", "1")
        os.environ.setdefault("ML_SKIP_FORECAST_ON_REPORT", "1")
        os.environ.setdefault("ENABLE_SCHEDULER", "0")

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
    Prefer a real Postgres URL when explicitly set.
    On serverless, always use the bundled demo SQLite under /tmp
    (project filesystem is read-only — relative sqlite paths crash).
    """
    existing = (os.environ.get("INVOICE_DB_URL") or "").strip()

    # Optional durable backend — only if a non-sqlite URL is configured.
    if existing.startswith("postgresql"):
        return

    if not is_serverless():
        return

    bundled = REPO_ROOT / "deploy" / "demo_ledger.db"
    dest = Path(os.environ.get("LEDGER_TMP_DB", "/tmp/ledger_demo.db"))
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Cold starts get an empty /tmp → copy bundled demo once. Keep an existing
    # /tmp DB across warm imports, but repair if the demo owner is missing
    # (corrupt / partial file, or a wiped ephemeral copy).
    need_copy = not dest.is_file() or dest.stat().st_size == 0
    if not need_copy and bundled.is_file() and not _sqlite_has_demo_owner(dest):
        need_copy = True
    if need_copy and bundled.is_file() and bundled.stat().st_size > 0:
        shutil.copy2(bundled, dest)
    elif not dest.is_file():
        dest.touch()

    # Always override — ignore relative sqlite:// URLs that would hit read-only FS.
    os.environ["INVOICE_DB_URL"] = f"sqlite:///{dest}"


def _sqlite_has_demo_owner(path: Path) -> bool:
    """True when the demo login user exists (signed tokens need this row)."""
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM store_users WHERE username = ? AND is_active = 1 LIMIT 1",
                ("owner",),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


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
