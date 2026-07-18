# Phase 2 — Exploratory Analytics Dashboard

A read-only dashboard sitting directly on top of the Phase 1 invoice-pipeline
database. Backend: FastAPI + the same SQLAlchemy models as Phase 1. Frontend:
a single self-contained HTML file (vanilla JS + Chart.js via CDN) — no build
step required.

## Running it

### 1. Backend

```bash
cd backend
pip install -r requirements.txt

# Point at your Phase 1 invoice-pipeline project and its database:
export INVOICE_PIPELINE_ROOT=/path/to/invoice-pipeline
export INVOICE_DB_URL="sqlite:////path/to/invoice-pipeline/invoice_pipeline.db"
# (or a postgresql+psycopg2://... URL if you've moved to Postgres)

python3 -m uvicorn main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
python3 -m http.server 8080
```

Open `http://localhost:8080/index.html`. It talks to the backend at
`http://localhost:8000` by default (see the `API_BASE` constant at the top
of `index.html` if you need to point it elsewhere, e.g. after deploying).

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
| `GET /api/stores` | Helper endpoint for the store-selector dropdown |

All endpoints accept `start_date`, `end_date`, and (except store-comparison)
`store_id` as query params, and return a consistent `{data, meta}` envelope.

## Frontend layout

Single-page dashboard, top to bottom:
1. **Filter bar** — date range + store selector, applies to every section
2. **KPI cards** — revenue, transactions, avg basket, units, with period-over-period change
3. **Sales trend** — line chart with moving average overlay
4. **Top products + category breakdown** — side by side
5. **Pareto chart** — combo bar+line with an "N products drive 80% of revenue" callout
6. **Day × hour heatmap**
7. **Store comparison table** — automatically hidden if only one store exists in the data
8. **Payment method breakdown**

Every section handles: loading state (shimmer skeleton), empty state (clear
message, not a blank chart), and API errors surface in the "last updated"
line rather than silently failing.

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

1. **Single organization, no auth** — this assumes one team/organization
   viewing their own data. Add an auth layer before exposing this beyond
   local/internal use.
2. **`invoice_time` may be sparse** — the heatmap degrades gracefully to a
   "not available" message if no invoices have a time recorded. Confirm
   whether your POS data captures time-of-day; if not, that's a data-capture
   gap worth fixing upstream, not just a dashboard limitation.
3. **Default date range** is "last 60 days from today" — change the
   `todayISO(-60)` call in `index.html`'s `init()` function if you want a
   different default window.
4. **In-memory cache is per-process** — fine for local/single-instance use;
   revisit if this gets deployed behind multiple backend replicas.
