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

# 4. Check what happened
python3 cli.py summary
python3 cli.py show-errors
```

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

## Design principles this pipeline follows

- **Never crash a whole batch on one bad file** — every file/invoice is isolated.
- **Never silently "fix" ambiguous data** — ambiguous matches are logged for human review, not auto-resolved.
- **Idempotent** — re-running the same file/folder does not create duplicate rows.
- **All-or-nothing per invoice** — an invoice's header + all its line items load in a single transaction.

## Running tests

```bash
python3 -m pytest tests/ -v
```

23 tests covering adapter parsing (CSV/PDF/OCR), cleaning functions
(number/date normalization, validation rules), and full end-to-end pipeline
behavior (idempotency, partial-failure isolation, error logging) against an
isolated temp database.

## Project structure

```
invoice-pipeline/
├── adapters/            # one file per source format, common interface
├── cleaning/             # normalization, date parsing, fuzzy product matching, validation
├── db/                   # SQLAlchemy models + session/engine setup
├── pipeline/             # orchestration (ingest.py), dedup logic, config loader
├── config/                # field_mappings.yaml, thresholds.yaml
├── tests/
│   ├── fixtures/           # sample CSV, PDF, and scanned-image invoices
│   ├── test_adapters.py
│   ├── test_cleaning.py
│   └── test_pipeline_e2e.py
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
