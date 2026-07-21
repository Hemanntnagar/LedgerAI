# Invoice Analytics Platform — Combined Build

This is the combined output of the first three build phases, packaged
together as one project. Each phase is a self-contained folder with its
own README, but they share the same underlying database.

> **Deploy the Ledger store app on Vercel:** see [`DEPLOY.md`](DEPLOY.md).
> Demo login after deploy: `owner` / `demo1234`.

```
invoice-analytics-platform/
├── invoice-pipeline/     Phase 1 — ingestion (CSV/PDF/OCR -> normalized DB)
├── dashboard/            Phase 2 — read-only analytics dashboard (FastAPI + HTML/Chart.js)
├── ml-models/            Phase 3 — forecasting, basket, ABC, elasticity, anomalies, + Step 5 modules
├── insight-engine/       Step 6 — deterministic ranked actions (+ Step 7 feedback weights)
├── api/                  Vercel FastAPI entry (api/index.py)
├── public/               Static Ledger UI for Vercel CDN
└── deploy/               Demo DB builder + serverless bootstrap
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

## Step 6 — Insight Engine

[`insight-engine/`](insight-engine/) turns ML result tables into a
ranked, quantified action list in `insights`. Scoring is
`impact × confidence × urgency` with YAML thresholds — **no Anthropic /
OpenAI**. Report generate (`POST /api/v1/reports`) runs ML then the
insight engine.

## Step 8 — Store app UI (single place)

[`dashboard/frontend/index.html`](dashboard/frontend/index.html) is now
the daily store app: login → **Action Center** (ranked actions +
feedback), Revenue (D/W/M), Intelligence (ML tables), Loyalty, Reports
(generate + MoM list), and Admin (subscription + POS API key rotate).

ML read APIs: `GET /api/v1/ml/{forecast,bundles,pricing,inventory,customers,festivals}`.
Admin: `GET|POST /api/v1/admin/api-key`.

## Step 10 — Pilot & prove value

Prove the Action Center on real history and one store:

- **Accepted-action outcomes** — baseline vs follow-up revenue around
  `resolved_at` (`GET /api/v1/pilot/outcomes`)
- **Historical correlation** — months with accepted actions vs next-month
  lift (`POST /api/v1/pilot/backtest`, logged as `pilot_backtest`)
- **Threshold tune** — simulate `rules.yaml` grids without persisting
  (`POST /api/v1/pilot/tune`; CLI `insight-engine/cli.py tune`)
- Defaults / festival suggestions: `invoice-pipeline/config/pilot.yaml`
- Admin → **Pilot** panel shows summary + recent before/after rows

```bash
cd insight-engine
python3 cli.py pilot-outcomes --store-id <uuid>
python3 cli.py backtest --store-id <uuid>
python3 cli.py tune --store-id <uuid> --rule bundle --param min_lift --values 1.1,1.2,1.3
```

Correlation is not causal proof — use it with Action Center feedback to
tune thresholds for Indian POS / festival season.

## Step 9 — Automation & monitoring

Nightly ML refresh for active subscriptions (respects each plan’s
`ml_refresh_hours`), forecast MAPE / confidence drift alerts, ingestion
error-rate checks, and a `job_runs` log.

```bash
# Cron (recommended):
cd ml-models
python3 cli.py nightly-refresh
python3 cli.py health-check

# Or in-process APScheduler on the API:
ENABLE_SCHEDULER=1 uvicorn main:app --port 8001
```

Store app **Admin → Monitoring** shows health, recent jobs, and open
alerts (`GET /api/v1/monitoring/*`). Thresholds live in
`invoice-pipeline/config/monitoring.yaml`.

## Step 7 — Monthly report + MoM learning loop

Report generate (`POST /api/v1/reports`) now stores richer
`report_snapshots`: KPIs, top actions, model versions, and a forecast
**MAPE summary**. A second consecutive month returns a **MoM** block
(revenue/transaction deltas + prior-period action outcomes from
feedback). Stores can mark insights `useful` / `not_useful`
(`PATCH /api/v1/insights/{id}/feedback`); the insight engine multiplies
YAML rule weights with a Beta-style prior (still no LLM). More invoice
history improves ML automatically — no separate training upload.

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
