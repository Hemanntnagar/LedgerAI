# Deploying Ledger

Ledger has two deploy modes:

| Mode | Where | Dynamic? |
|------|--------|----------|
| **Demo (read-only)** | Vercel + bundled SQLite | Browse pre-seeded Action Center / Revenue / ML tables. Heavy refresh returns **501**. |
| **Full dynamic** | Render (or similar) + Postgres + worker | Register stores, upload invoices, queue ML + insights. |

For local development, use `./scripts/dev.sh` (UI + API on http://127.0.0.1:8000/).

---

## Full dynamic (Render + Postgres) — recommended for real use

[`render.yaml`](render.yaml) defines:

- **ledger-api** — FastAPI web service (serves UI + API, `ANALYTICS_ASYNC=1`)
- **ledger-worker** — polls queued `analytics_refresh` jobs and runs rollup + ML + insights
- **ledger-db** — Postgres

### Steps

1. Connect the repo in [Render](https://render.com) → Blueprint → apply `render.yaml`.
2. Confirm `INVOICE_DB_URL` is wired from `ledger-db` on both web and worker.
3. One-time schema init (Render shell on web or worker):

   ```bash
   export INVOICE_PIPELINE_ROOT=$PWD/invoice-pipeline
   export INVOICE_DB_URL=…   # already set in service env
   cd invoice-pipeline && python cli.py init-db-cmd
   ```

4. Open the web service URL → **Create an account** → upload CSV → **Run analytics now**.
   With `ANALYTICS_ASYNC=1`, refresh returns `status: queued`; the worker processes it. Check Admin → Jobs.

5. Set a strong `LEDGER_SESSION_SECRET` (Blueprint can generate one) shared by web + worker.

`postgres://` URLs from Render are normalized to `postgresql+psycopg2://` in [`invoice-pipeline/db/session.py`](invoice-pipeline/db/session.py).

### Worker locally

```bash
export INVOICE_DB_URL='postgresql+psycopg2://…'
export INVOICE_PIPELINE_ROOT="$(pwd)/invoice-pipeline"
export ML_MODELS_ROOT="$(pwd)/ml-models"
export INSIGHT_ENGINE_ROOT="$(pwd)/insight-engine"
export ANALYTICS_ASYNC=1
python deploy/worker.py
```

---

## Vercel demo (read-only public link)

Ledger’s store UI + read API run on Vercel as a FastAPI serverless function
with a bundled SQLite demo database. Heavy ML (`run-all`, report generate,
nightly refresh, pilot backtest) is **disabled** on serverless (returns 501).

### One-time setup (no env vars required)

1. Install the [Vercel CLI](https://vercel.com/docs/cli) and log in:
   ```bash
   npm i -g vercel
   vercel login
   ```
2. From the repo root, deploy — **do not set** `INVOICE_DB_URL` or other secrets.
   The build syncs `public/`; cold starts copy `deploy/demo_ledger.db` → `/tmp`.
   ```bash
   vercel          # preview
   vercel --prod   # production
   ```
3. Optional local rebuild of the demo DB (also runs if the file is missing on Vercel build):
   ```bash
   python deploy/build_demo_db.py
   python deploy/vercel_build.py
   ```

### Demo login

| Field | Value |
|---|---|
| Username | `owner` |
| Password | `demo1234` |
| Store | Corner Kirana (Pro) |

### What gets deployed

| Piece | Role |
|---|---|
| `public/index.html` | Ledger UI (CDN) |
| `api/index.py` | FastAPI entry (`app`) |
| `deploy/demo_ledger.db` | Copied to `/tmp` on cold start |
| `requirements.txt` | Slim Python deps (no Prophet / OCR) |

Env flags set automatically on Vercel: `SKIP_ML_ON_REPORT`, `SKIP_INSIGHTS_ON_REPORT`, `ENABLE_SCHEDULER=0`.

### Optional: Postgres on Vercel (durable reads only)

For a durable multi-user **read** demo, create a Neon / Supabase / Vercel Postgres DB, set:

```bash
vercel env add INVOICE_DB_URL
# postgresql+psycopg2://USER:PASS@HOST/DB
```

Then seed once from your machine:

```bash
export INVOICE_DB_URL='postgresql+psycopg2://…'
export INVOICE_PIPELINE_ROOT="$(pwd)/invoice-pipeline"
export ML_MODELS_ROOT="$(pwd)/ml-models"
cd invoice-pipeline && python cli.py init-db-cmd
# ingest, set-password, run ML offline, etc.
```

When `INVOICE_DB_URL` is set, the bundled SQLite file is ignored.
**ML refresh still returns 501 on Vercel** — use Render + worker for full dynamic.

### Local serverless smoke test

```bash
LEDGER_SERVERLESS=1 VERCEL=1 \
  python -c "from api.index import app; print(app.title)"
# or: vercel dev
```

### Limits (honest)

- Report **Generate**, Admin **Refresh ML**, and Pilot **backtest/tune** return HTTP 501 on Vercel
  (disabled on serverless — not a crash). Browse Action Center / Revenue / Intelligence instead.
- Login uses **signed session tokens** so auth survives `/tmp` SQLite cold-start resets.
  Feedback / other writes to SQLite can still reset on cold start; use Postgres for durability.
- Full forecasting / OCR stays a local or worker concern (`ml-models/`, `invoice-pipeline/`, `deploy/worker.py`).
