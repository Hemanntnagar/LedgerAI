# Phase 2 — Exploratory Analytics Dashboard

A read-only dashboard sitting directly on top of the Phase 1 invoice-pipeline
database. Backend: FastAPI + the same SQLAlchemy models as Phase 1. Frontend:
a single self-contained HTML file (vanilla JS + Chart.js via CDN) — no build
step required.

## Running it

### Quick start (recommended)

From the repo root:

```bash
chmod +x scripts/dev.sh
./scripts/dev.sh
```

Open **http://127.0.0.1:8000/** — FastAPI serves the UI and API on the same origin.
The script initializes a local SQLite DB on first run.

### Manual backend

```bash
cd backend
pip install -r requirements.txt

# Point at your Phase 1 invoice-pipeline project and its database:
export INVOICE_PIPELINE_ROOT=/path/to/invoice-pipeline
export INVOICE_DB_URL="sqlite:////path/to/invoice-pipeline/invoice_pipeline.db"
# (or a postgresql+psycopg2://... URL if you've moved to Postgres)

python3 -m uvicorn main:app --reload --port 8000
```

Open `http://127.0.0.1:8000/`. If you serve the HTML from another port, it
talks to the API at `http://localhost:8000` (see `API_BASE` in `index.html`).
## What's implemented

All 8 metric endpoints from the spec, each backed by a real SQL aggregation
query (not pandas post-processing):

| Endpoint | What it returns |
|---|---|
| `GET /api/kpis` | Revenue, transactions, avg basket value, units sold, % change vs previous period |
| `GET /api/sales-trend` | Daily/weekly/monthly revenue + 7-period moving average |
| `GET /api/top-products` | Top N products by revenue or units |
| `GET /api/pareto` | Products ranked by revenue with cumulative % and the "N products = 80% of revenue" cutoff |
| `GET /api/category-breakdown` | Revenue/units by product category |
| `GET /api/time-heatmap` | Revenue by day-of-week × hour (degrades to day-of-week only if invoice_time isn't populated) |
| `GET /api/store-comparison` | Revenue/transactions/basket value per store |
| `GET /api/payment-methods` | Revenue/transaction count by payment method |
| `GET /api/revenue` | Day / week / month revenue series + totals (facts or live SQL) |
| `GET /api/stores` | Helper endpoint for the store-selector dropdown |
| `POST /api/v1/invoices` | Live POS ingest (auth: `X-API-Key`). See invoice-pipeline Step 1. |
| `POST /api/v1/auth/register` | Create store + owner account → session token |
| `POST /api/v1/auth/login` | Store dashboard login → session token |
| `GET /api/v1/auth/me` | Current store user (requires Bearer token) |
| `POST /api/v1/uploads/invoices` | Browser multipart upload (CSV/JSON/PDF; forced to signed-in store) |
| `POST /api/v1/monitoring/refresh` | Rollup + ML + insights (or queue when `ANALYTICS_ASYNC=1`) |
| `GET /api/v1/subscription` | Plan, status, report quota remaining |
| `POST /api/v1/reports` | Generate report snapshot (checks active sub + monthly quota; Step 7 MAPE + MoM) |
| `GET /api/v1/reports` | List prior snapshots for the store (newest first, with MoM) |
| `GET /api/v1/insights` | Ranked actions for the authenticated store only |
| `PATCH /api/v1/insights/{id}/feedback` | Mark useful / not_useful (tunes future rule weights) |
| `GET /api/v1/loyalty/lookup` | Loyalty balance / tier by phone (Pro; session or API key) |
| `GET /api/v1/loyalty/rewards` | Reward catalog |
| `POST /api/v1/loyalty/redeem` | Redeem points for a catalog reward |

All endpoints accept `start_date`, `end_date`, and (except store-comparison)
`store_id` as query params, and return a consistent `{data, meta}` envelope.
With a store session (`Authorization: Bearer st_…`), analytics are forced to
that store — cross-store access returns 403.

### Step 3 — Revenue period views

```bash
# After ingesting invoices, roll up facts (from invoice-pipeline):
python3 cli.py rollup-revenue

# Day / week / month (prefers facts when present)
curl 'http://localhost:8000/api/revenue?start_date=2026-06-01&end_date=2026-07-31&granularity=week'
```

The Sales Trend panel has a **Day / Week / Month** toggle that calls this
endpoint. Response includes `source` (`facts` or `live`) and `totals`.

### Step 4 — Loyalty

Pro stores only (`features_json.loyalty`). Lookup / redeem accept either a
dashboard session or the POS `X-API-Key`.

```bash
curl 'http://localhost:8000/api/v1/loyalty/lookup?phone=9876543210' \
  -H "Authorization: Bearer st_..."
```

### Step 5 — ML on every report

`POST /api/v1/reports` runs `ml-models` `run-all` for the authenticated
store (forecast included unless `ML_SKIP_FORECAST_ON_REPORT=1`). Set
`SKIP_ML_ON_REPORT=1` to skip. Point `ML_MODELS_ROOT` at the ml-models
folder if it is not a sibling of `dashboard/`.

### Step 6 — Insight Engine (ranked actions)

After ML refresh, report generate runs the deterministic
[`insight-engine/`](../insight-engine/) package: rules + scoring →
`insights` table. `GET /api/v1/insights` returns the ranked list.
Set `SKIP_INSIGHTS_ON_REPORT=1` to skip. Point `INSIGHT_ENGINE_ROOT`
at the package if it is not a sibling of `dashboard/`.

### Step 7 — MoM learning loop

Each generated snapshot stores `mape_summary` (from `forecast_results`
when present). Generating a second month returns `mom` with KPI deltas
vs the previous snapshot and tallies of prior-period action feedback.

```bash
# Mark an action useful → raises that action_type weight next run
curl -X PATCH http://localhost:8000/api/v1/insights/<id>/feedback \
  -H "Authorization: Bearer st_..." \
  -H "Content-Type: application/json" \
  -d '{"feedback":"useful","status":"accepted"}'

curl http://localhost:8000/api/v1/reports -H "Authorization: Bearer st_..."
```


### Step 8 — Store app UI

Sign in via `POST /api/v1/auth/login`, then use the Action Center as home.
Intelligence tabs call `/api/v1/ml/*`. Admin rotates the POS key with
`POST /api/v1/admin/api-key` (raw key shown once).

```bash
# ML summary for the signed-in store
curl http://localhost:8000/api/v1/ml/summary -H "Authorization: Bearer st_..."

# Rotate POS API key
curl -X POST http://localhost:8000/api/v1/admin/api-key \
  -H "Authorization: Bearer st_..."
```

### Step 9 — Automation & monitoring

| Endpoint | What it returns |
|---|---|
| `GET /api/v1/monitoring/health` | Ingestion error rate + MAPE/confidence for this store |
| `GET /api/v1/monitoring/jobs` | Recent `job_runs` (store + platform batch) |
| `GET /api/v1/monitoring/alerts` | Open (or filtered) monitoring alerts |
| `POST /api/v1/monitoring/alerts/{id}/ack` | Acknowledge / resolve an alert |
| `POST /api/v1/monitoring/refresh` | Manual ML refresh (same path as nightly) |

```bash
# Prefer system cron for production:
cd ../ml-models && python3 cli.py nightly-refresh

# Optional in-process scheduler (02:00 Asia/Kolkata by default):
ENABLE_SCHEDULER=1 python3 -m uvicorn main:app --port 8000
```

Thresholds: `invoice-pipeline/config/monitoring.yaml`.

### Step 10 — Pilot & prove value

| Endpoint | What it returns |
|---|---|
| `GET /api/v1/pilot/summary` | Feedback tallies + outcome aggregates + month correlation |
| `GET /api/v1/pilot/outcomes` | Per accepted/done insight: baseline vs follow-up ₹ |
| `POST /api/v1/pilot/backtest` | Correlation run + `job_runs` (`pilot_backtest`) |
| `POST /api/v1/pilot/tune` | Threshold grid for one rule (dry-run) |

Config: `invoice-pipeline/config/pilot.yaml` (window lengths, festival
override suggestions). CLI mirrors live in `insight-engine/cli.py`
(`pilot-outcomes`, `backtest`, `tune`).


## Frontend layout (Step 8 store app)

Single HTML app with store login. Nav views:

1. **Action Center** — ranked insights; accept / dismiss / useful feedback
2. **Revenue** — KPIs, D/W/M trend, top products, category, Pareto, heatmap, payments
3. **Intelligence** — forecast, bundles, pricing, inventory, customers, festivals
4. **Loyalty** — phone lookup, reward catalog, redeem (Pro)
5. **Reports** — quota, generate snapshot, prior months + MoM
6. **Admin** — subscription status, POS API key, monitoring (jobs / alerts / refresh), **Pilot** (outcomes + correlation)

Every section handles loading, empty, and error states.

## Design notes

Styled as "Ledger" — a small ledger-book identity (ink sidebar, warm paper
background, monospace figures for all numeric data since this is fundamentally
an accounting/invoice tool) rather than a generic dashboard template. The
signature element is the teal rule down the left edge of each KPI card,
echoing a ledger line.

## Testing

```bash
cd backend
export INVOICE_PIPELINE_ROOT=/path/to/invoice-pipeline
python3 -m pytest tests/ -v
```

8 tests, all against an isolated temp SQLite DB seeded with known values —
assertions check exact numbers (e.g. "2 invoices totaling ₹300 average to
₹150 basket value"), not just "did it return 200."

## Performance & caching

KPI and sales-trend queries are cached in-memory for 5 minutes (dashboards
get refreshed/polled often; the underlying invoice data doesn't change that
fast). Swap `cache.py`'s `TTLCache` for Redis if this needs to run
multi-process/multi-instance — the interface is a single `get_or_set()` call,
easy to swap.

## Known assumptions / what to confirm

1. **Store login for tenant features** — Action Center, ML, loyalty,
   reports, and admin require a store session. Unauthenticated access still
   works for local demo analytics (`/api/kpis` etc.).
2. **`invoice_time` may be sparse** — the heatmap degrades gracefully to a
   "not available" message if no invoices have a time recorded. Confirm
   whether your POS data captures time-of-day; if not, that's a data-capture
   gap worth fixing upstream, not just a dashboard limitation.
3. **Default date range** is "last 60 days from today" — change the
   `todayISO(-60)` call in `index.html`'s `init()` function if you want a
   different default window.
4. **In-memory cache is per-process** — fine for local/single-instance use;
   revisit if this gets deployed behind multiple backend replicas.
