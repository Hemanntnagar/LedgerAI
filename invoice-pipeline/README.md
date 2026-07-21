# Invoice Ingestion Pipeline (Phase 1)

Ingests invoices from CSV/JSON exports, text-based PDFs, or scanned images,
validates and cleans the data, and loads it into a normalized relational
schema ready for analytics and ML (later phases).

## Setup

```bash
pip install -r requirements.txt
# Tesseract binary is required for OCR support:
apt-get install tesseract-ocr   # already present in most environments
```

By default this uses a local SQLite file (`invoice_pipeline.db`) so you can
run it with zero infrastructure. To point at PostgreSQL instead:

```bash
export INVOICE_DB_URL="postgresql+psycopg2://user:password@host:5432/invoice_db"
```

No code changes needed — all models use standard SQLAlchemy column types.

## Quick start

```bash
# 1. Create the tables
python3 cli.py init-db-cmd

# 2. Ingest a single file
python3 cli.py ingest --file tests/fixtures/sample_invoices.csv --type csv
python3 cli.py ingest --file tests/fixtures/sample_invoice.pdf --type pdf
python3 cli.py ingest --file tests/fixtures/sample_invoice_scan.png --type ocr_image

# 3. Ingest a whole folder at once
python3 cli.py batch-ingest-cmd --folder path/to/invoices --type csv

# 4. Issue a live POS API key (Step 1)
python3 cli.py issue-api-key --store-name "My Store"

# 5. Check what happened
python3 cli.py summary
python3 cli.py show-errors
```

## Live POS ingest (Step 1)

After issuing an API key, push invoices to the dashboard backend:

```bash
curl -X POST http://localhost:8001/api/v1/invoices \
  -H "Content-Type: application/json" \
  -H "X-API-Key: inv_..." \
  -d '{
    "invoice_number": "POS-1001",
    "invoice_date": "2026-07-18",
    "customer_phone": "9876543210",
    "grand_total": 150,
    "line_items": [
      {"product_name": "Basmati Rice 5kg", "quantity": 1, "unit_price": 150}
    ]
  }'
```

Same phone across visits resolves to one customer. File ingest (`ingest` /
`batch-ingest`) remains the path for bulk history and stores without a POS API.

## Supported input types

| `--type` | Source | Notes |
|---|---|---|
| `csv` | Structured POS/ERP export | One row per line item; grouped into invoices by (invoice_number, date, store) |
| `json` | Structured export | Accepts either a flat list of line-item objects or `{"invoices": [...]}` |
| `pdf` | Text-based PDF | Uses `pdfplumber` for table detection + regex for header fields |
| `ocr_image` | Scanned photo/image | Uses Tesseract OCR; every invoice gets a `confidence` score |

## Adding a new POS export format

Don't write new adapter code — add a new profile to `config/field_mappings.yaml`:

```yaml
my_new_pos:
  invoice_number: ["Bill Ref"]
  invoice_date: ["Txn Date"]
  # ... etc, list every raw column name variant this source uses
```

Then ingest with `--mapping-profile my_new_pos`.

## Tuning thresholds

`config/thresholds.yaml` controls:
- Fuzzy product-name matching cutoffs (auto-merge vs. flag-for-review vs. new product)
- Outlier detection sensitivity (price z-score threshold)
- What counts as invalid data (negative quantities, absurd quantities, etc.)

Change these without touching code.

## Reading the `ingestion_errors` table

Every problem the pipeline finds is logged here rather than silently dropped
or auto-fixed. Common `error_type` values:

- `parse_failure` — the file itself couldn't be read/parsed at all
- `invoice_validation_failed` — the whole invoice was rejected (see `error_detail` for which rule(s) failed: missing date, no valid line items, negative quantity/price)
- `line_item_validation_failed` — one or more individual line items were skipped, but the rest of the invoice still loaded if it had valid items remaining
- `ambiguous_product_match` — a product name scored in the "unclear" band against an existing product; it was created as a new product rather than silently merged — review and merge manually if it's actually a duplicate
- `outlier_review` — a unit price is a statistical outlier vs. that product's history; the row is still loaded, just flagged
- `unexpected_error` — anything uncaught; check `error_detail` for the exception message

## Step 0 — Data contracts & schema extensions

Invoice payloads (file ingest or future live API) should satisfy
`contracts/invoice_contract.py`:

| Field | Required |
|---|---|
| `store_id` or `store_name` | yes |
| `invoice_number`, `invoice_date`, line items, `grand_total` | yes |
| `customer_phone` (Indian mobile → `+91…` E.164) | required for loyalty plans |
| offer codes, payment method, unit cost / stock | optional |

New / hardened tables (same SQLAlchemy `Base`, created by `init-db`):

- `customers.phone_normalized` — unique loyalty identity
- `offers`, `invoice_offers` (+ optional `invoice_line_items.offer_id`)
- `subscriptions` — plan, API key hash, report quotas
- `loyalty_accounts`, `loyalty_transactions`
- `festival_events` — Indian calendar seed (2025–2027)
- `insights`, `report_snapshots`
- `daily_revenue_facts`, `monthly_revenue_facts` (Step 3 rollups)

```bash
python3 cli.py init-db-cmd          # creates tables + seeds festivals
python3 cli.py seed-festivals       # idempotent re-seed
```

SQLite remains the local default; point `INVOICE_DB_URL` at PostgreSQL for
multi-tenant production (no model changes required).

## Step 2 — Multi-tenant subscriptions

Plans (`free` / `basic` / `pro`), store dashboard login, and report quotas:

```bash
python3 cli.py init-db-cmd
python3 cli.py issue-api-key --store-name "Corner Kirana" --plan basic
python3 cli.py set-password --store-name "Corner Kirana" --username owner --password secret1
python3 cli.py set-plan --store-name "Corner Kirana" --plan pro
```

Dashboard (with backend running):

```bash
# login → session token
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"secret1"}'

# scoped KPIs (other stores → 403)
curl 'http://localhost:8001/api/kpis?start_date=2026-07-01&end_date=2026-07-31' \
  -H "Authorization: Bearer st_..."

# generate report (402 if monthly quota exhausted)
curl -X POST http://localhost:8001/api/v1/reports \
  -H "Authorization: Bearer st_..." \
  -H 'Content-Type: application/json' \
  -d '{"period_start":"2026-07-01","period_end":"2026-07-31","period_type":"monthly"}'
```

## Step 3 — Revenue facts (day / week / month)

Roll invoices into stable aggregates so D/W/M numbers stay consistent:

```bash
python3 cli.py init-db-cmd
python3 cli.py rollup-revenue
# optional filters:
python3 cli.py rollup-revenue --store-name "Corner Kirana" --start-date 2026-01-01 --end-date 2026-07-31
```

- `daily_revenue_facts` — per store × date (source for week views)
- `monthly_revenue_facts` — month-end totals + category/product JSON; `is_frozen` when the month has ended

Dashboard: `GET /api/revenue?granularity=day|week|month` (prefers facts after rollup; falls back to live SQL).

## Step 4 — Phone loyalty & rewards

Loyalty identity is the customer's normalized phone. Points accrue on live
ingest (Pro plan); tiers flip from lifetime spend or visit count.

```bash
python3 cli.py init-db-cmd
python3 cli.py issue-api-key --store-name "Corner Kirana" --plan pro
# after invoices with customer_phone:
python3 cli.py backfill-loyalty --store-name "Corner Kirana"
python3 cli.py loyalty-lookup --phone 9876543210 --store-name "Corner Kirana"
```

Rules live in `config/loyalty.yaml` (₹10 → 1 point, 2× during festival
windows, bronze/silver/gold thresholds, reward catalog).

Dashboard / POS:

```bash
# lookup at checkout (API key or store session)
curl 'http://localhost:8001/api/v1/loyalty/lookup?phone=9876543210' \
  -H "X-API-Key: inv_..."

curl http://localhost:8001/api/v1/loyalty/rewards -H "X-API-Key: inv_..."

curl -X POST http://localhost:8001/api/v1/loyalty/redeem \
  -H "X-API-Key: inv_..." \
  -H 'Content-Type: application/json' \
  -d '{"phone":"9876543210","reward_code":"DISCOUNT5"}'
```

Live ingest responses include `loyalty_points_earned`, `loyalty_points_balance`,
and `loyalty_tier` when points accrue.

## Step 5 — ML modules (run-all)

ML lives in the sibling [`ml-models/`](../ml-models/) package. On each
dashboard report generate, that store’s data is re-run through all modules
(festival intelligence, customer RFM, inventory signals, forecast with
MAPE-based confidence, etc.).

```bash
cd ../ml-models
export INVOICE_PIPELINE_ROOT=$(pwd)/../invoice-pipeline
python3 cli.py run-all --store-name "Corner Kirana"
python3 cli.py run-festival --store-name "Corner Kirana"
```

See `ml-models/README.md` for module list and config.

## Step 6 — Insight Engine (ranked actions)

The sibling [`insight-engine/`](../insight-engine/) package turns ML
result tables into a scored action list in `insights` (no external LLM).

```bash
cd ../insight-engine
export INVOICE_PIPELINE_ROOT=$(pwd)/../invoice-pipeline
export ML_MODELS_ROOT=$(pwd)/../ml-models
python3 cli.py run --store-name "Corner Kirana"
```

Dashboard `POST /api/v1/reports` runs this after `run-all`.

## Step 9 — Automation & monitoring

Schema: `job_runs`, `monitoring_alerts`. Config:
`config/monitoring.yaml` (MAPE drift, confidence collapse, ingestion
error rate). Nightly refresh lives in `ml-models`:

```bash
cd ../ml-models
python3 cli.py nightly-refresh   # due active subscriptions
python3 cli.py health-check      # ingestion + MAPE alerts
```

## Step 10 — Pilot & prove value

Config: `config/pilot.yaml` (baseline/follow-up windows, festival
threshold suggestions). Helpers in `db/pilot.py`:

- `accepted_action_outcomes` — revenue before/after accepted actions
- `historical_action_correlation` — acted vs idle months → next-month lift

```bash
cd ../insight-engine
python3 cli.py pilot-outcomes --store-id <uuid>
python3 cli.py backtest --store-id <uuid>
python3 cli.py tune --store-id <uuid> --rule bundle --param min_lift --values 1.1,1.2,1.3
```

## Design principles this pipeline follows

- **Never crash a whole batch on one bad file** — every file/invoice is isolated.
- **Never silently "fix" ambiguous data** — ambiguous matches are logged for human review, not auto-resolved.
- **Idempotent** — re-running the same file/folder does not create duplicate rows.
- **All-or-nothing per invoice** — an invoice's header + all its line items load in a single transaction.

## Running tests

```bash
python3 -m pytest tests/ -v
```

Tests cover adapter parsing (CSV/PDF/OCR), cleaning functions
(number/date normalization, validation rules), Step 0 schema/contracts/phone
normalization, and full end-to-end pipeline behavior (idempotency,
partial-failure isolation, error logging) against an isolated temp database.

## Project structure

```
invoice-pipeline/
├── adapters/            # one file per source format, common interface
├── cleaning/             # normalization, date parsing, fuzzy product matching, validation
├── contracts/            # Step 0 invoice data contracts (Pydantic)
├── db/                   # SQLAlchemy models, phone utils, festival seed, session
├── pipeline/             # orchestration (ingest.py), dedup logic, config loader
├── config/                # field_mappings.yaml, thresholds.yaml
├── tests/
│   ├── fixtures/           # sample CSV, PDF, and scanned-image invoices
│   ├── test_adapters.py
│   ├── test_cleaning.py
│   ├── test_pipeline_e2e.py
│   └── test_step0_schema.py
└── cli.py                 # command-line entrypoint
```
## Known assumptions / what to confirm before production use

1. **Date format priority**: when a date string is ambiguous between
   DD/MM/YYYY and MM/DD/YYYY (e.g. "05/01/2026"), the parser tries DD/MM/YYYY
   first. Confirm this matches your actual data source's convention, or invoices
   could silently get the wrong date. Safer long-term: ensure your source
   exports ISO 8601 (`YYYY-MM-DD`) if at all possible — that format is
   unambiguous and is tried first.
2. **Fuzzy match thresholds** (90 auto-merge / 75 review-band) are reasonable
   defaults but should be tuned against a sample of your real product catalog
   — very short product names (e.g. "Milk" vs "Milk ") behave differently
   under fuzzy matching than long descriptive names.
3. **PDF/OCR adapters are heuristic** — real invoice layouts vary widely.
   They were validated against the sample fixtures included here; expect to
   iterate on the regex patterns in `adapters/pdf_adapter.py` and
   `adapters/ocr_adapter.py` once you feed in your actual invoice layouts.
4. **No object storage integration yet** — `source_file_path` currently
   points to a local filesystem path. Swap this for an S3/GCS path + upload
   step when moving beyond local/demo use.
5. **Alembic migrations not yet wired up** — table creation currently uses
   `Base.metadata.create_all()` for simplicity. Add Alembic before this touches
   a real production database, so future schema changes are versioned.
