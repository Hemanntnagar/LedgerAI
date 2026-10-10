#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export INVOICE_PIPELINE_ROOT="${INVOICE_PIPELINE_ROOT:-$ROOT/invoice-pipeline}"
export ML_MODELS_ROOT="${ML_MODELS_ROOT:-$ROOT/ml-models}"
export INSIGHT_ENGINE_ROOT="${INSIGHT_ENGINE_ROOT:-$ROOT/insight-engine}"
export LEDGER_FRONTEND_DIR="${LEDGER_FRONTEND_DIR:-$ROOT/dashboard/frontend}"
export ANALYTICS_ASYNC="${ANALYTICS_ASYNC:-1}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-0}"
cd "$ROOT/dashboard/backend"
exec python -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
