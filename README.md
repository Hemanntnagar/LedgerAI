# Invoice Analytics Platform — Combined Build

This is the combined output of the first three build phases, packaged
together as one project. Each phase is a self-contained folder with its
own README, but they share the same underlying database.

```
invoice-analytics-platform/
├── invoice-pipeline/     Phase 1 — ingestion (CSV/PDF/OCR -> normalized DB)
├── dashboard/            Phase 2 — read-only analytics dashboard (FastAPI + HTML/Chart.js)
└── ml-models/            Phase 3 — forecasting, basket analysis, ABC, elasticity, anomalies
```

## Recommended setup order

1. **`invoice-pipeline/`** — set this up first and ingest your invoice data.
   See `invoice-pipeline/README.md`.
2. **`dashboard/`** — point its backend at the same database
   (`INVOICE_DB_URL` / `INVOICE_PIPELINE_ROOT` env vars) to visualize
   what's been ingested. See `dashboard/README.md`.
3. **`ml-models/`** — point it at the same database
   (`INVOICE_PIPELINE_ROOT` env var) to run forecasting, basket analysis,
   ABC classification, price elasticity, and anomaly detection. See
   `ml-models/README.md`.

All three share the same SQLAlchemy `Base`/engine, so they read and write
to the same physical database — no data duplication or syncing needed
between phases.

## What's NOT included in this bundle

Phase 4 (the insight/recommendation engine that turns Phase 3's model
outputs into a prioritized action list) was in progress but not yet
complete when this bundle was assembled — the rules, scoring, and LLM
digest logic exist in earlier conversation turns but weren't packaged.
Phase 5 (frontend UI) and Phases 6-7 (automation/testing/pilot) haven't
been built as working code yet — only the AI build prompts for those
phases were produced (see the standalone prompt `.md` files from earlier
in the conversation, if you still have them).

## Honest caveats carried over from Phase 3 testing

Worth reading before trusting any of this on real business decisions:

- **Forecasting accuracy was mediocre** (~74% MAPE) on the synthetic
  dataset used for testing — traced to naturally noisy day-level demand
  at this data volume, not a code bug. See `ml-models/README.md` for the
  full explanation and the recommended fix (wire backtest MAPE into the
  forecast confidence flag).
- **Basket analysis and price elasticity results were weak/mostly
  "unreliable"** on the synthetic test data, because that data was
  generated with random noise and no true purchase-affinity or
  price-demand signal — this is expected and should look different on
  real transaction data.
- **No customer-level modules** (segmentation, churn) — the connected
  dataset had no `customer_id` populated, so those were correctly
  skipped rather than faked.

Full details, including exact numbers, are in `ml-models/README.md`.
