#!/usr/bin/env bash
# Local Ledger: init SQLite DB (if needed) and run FastAPI + UI on :8000
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export INVOICE_PIPELINE_ROOT="${INVOICE_PIPELINE_ROOT:-$ROOT/invoice-pipeline}"
export ML_MODELS_ROOT="${ML_MODELS_ROOT:-$ROOT/ml-models}"
export INSIGHT_ENGINE_ROOT="${INSIGHT_ENGINE_ROOT:-$ROOT/insight-engine}"
export LEDGER_FRONTEND_DIR="${LEDGER_FRONTEND_DIR:-$ROOT/dashboard/frontend}"

DB_FILE="${LEDGER_DB_FILE:-$ROOT/invoice-pipeline/invoice_pipeline.db}"
export INVOICE_DB_URL="${INVOICE_DB_URL:-sqlite:///$DB_FILE}"

echo "==> Ledger local"
echo "    INVOICE_DB_URL=$INVOICE_DB_URL"
echo "    UI + API → http://127.0.0.1:8000/"

if [[ ! -f "$DB_FILE" ]] && [[ "$INVOICE_DB_URL" == sqlite* ]]; then
  echo "==> Initializing database…"
  (
    cd "$INVOICE_PIPELINE_ROOT"
    python3 cli.py init-db-cmd
  )
fi

cd "$ROOT/dashboard/backend"
exec python3 -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
