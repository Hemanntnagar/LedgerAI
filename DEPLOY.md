# Deploy Ledger on Vercel

Ledger’s store UI + read API run on Vercel as a FastAPI serverless function
with a bundled SQLite demo database. Heavy ML (`run-all`, report generate,
nightly refresh, pilot backtest) is **disabled** on serverless (returns 501).

## One-time setup (no env vars required)

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

## Demo login

| Field | Value |
|---|---|
| Username | `owner` |
| Password | `demo1234` |
| Store | Corner Kirana (Pro) |

Paste the production URL into Devpost as your **Try it out** link.

## What gets deployed

| Piece | Role |
|---|---|
| `public/index.html` | Ledger UI (CDN) |
| `api/index.py` | FastAPI entry (`app`) |
| `deploy/demo_ledger.db` | Copied to `/tmp` on cold start |
| `requirements.txt` | Slim Python deps (no Prophet / OCR) |

Env flags set automatically on Vercel: `SKIP_ML_ON_REPORT`, `SKIP_INSIGHTS_ON_REPORT`, `ENABLE_SCHEDULER=0`.

## Optional: Postgres instead of SQLite

For a durable multi-user demo, create a Neon / Supabase / Vercel Postgres DB, set:

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

## Local serverless smoke test

```bash
LEDGER_SERVERLESS=1 VERCEL=1 \
  python -c "from api.index import app; print(app.title)"
# or: vercel dev
```

## Limits (honest)

- Report **Generate**, Admin **Refresh ML**, and Pilot **backtest/tune** return HTTP 501 on Vercel.
- SQLite on `/tmp` resets on cold starts — fine for judges clicking around; use Postgres for persistence.
- Full forecasting / OCR stays a local or worker concern (`ml-models/`, `invoice-pipeline/`).
