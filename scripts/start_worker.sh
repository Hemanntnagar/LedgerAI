#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export INVOICE_PIPELINE_ROOT="${INVOICE_PIPELINE_ROOT:-$ROOT/invoice-pipeline}"
export ML_MODELS_ROOT="${ML_MODELS_ROOT:-$ROOT/ml-models}"
export INSIGHT_ENGINE_ROOT="${INSIGHT_ENGINE_ROOT:-$ROOT/insight-engine}"
cd "$ROOT"
exec python deploy/worker.py
