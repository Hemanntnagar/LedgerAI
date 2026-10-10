"""
Main pipeline orchestration: raw file -> validated, cleaned, loaded data.

Entry points:
    ingest_file(file_path, source_type, mapping_profile) -> IngestResult
    batch_ingest(folder_path, source_type, mapping_profile) -> BatchIngestResult

Design principles carried through from the spec:
- Never crash the whole batch because one file/invoice is bad.
- Never silently drop or "fix" ambiguous data -- log it to ingestion_errors.
- Each invoice (header + all its line items) loads in a single DB
  transaction: all-or-nothing.
- Idempotent: re-running the same file does not create duplicate rows.
"""
import logging
import os
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

from adapters.base import RawInvoiceData
from adapters.csv_json_adapter import CSVJSONAdapter
from adapters.pdf_adapter import PDFTextAdapter
from adapters.ocr_adapter import OCRImageAdapter
from cleaning.date_parser import parse_date, parse_time
from cleaning.normalizer import clean_number
from cleaning.product_matcher import ProductMatcher
from cleaning.validators import validate_invoice, validate_line_item
from db.models import Store, Customer, Invoice, InvoiceLineItem, IngestionError
from db.phone import InvalidPhoneError, normalize_phone
from db.session import get_session
from pipeline.dedup import file_hash, is_duplicate_by_natural_key, is_duplicate_by_file_hash
from pipeline.config_loader import load_thresholds

logger = logging.getLogger("invoice_pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class IngestResult:
    file_path: str
    invoices_found: int = 0
    invoices_loaded: int = 0
    invoices_skipped_duplicate: int = 0
    invoices_failed_validation: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class BatchIngestResult:
    files_processed: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    per_file_results: List[IngestResult] = field(default_factory=list)

    def summary(self) -> str:
        total_loaded = sum(r.invoices_loaded for r in self.per_file_results)
        total_dupes = sum(r.invoices_skipped_duplicate for r in self.per_file_results)
        total_failed_val = sum(r.invoices_failed_validation for r in self.per_file_results)
        return (
            f"Processed {self.files_processed} files: "
            f"{self.files_succeeded} succeeded, {self.files_failed} failed to process.\n"
            f"Invoices loaded: {total_loaded} | "
            f"Duplicates skipped: {total_dupes} | "
            f"Failed validation: {total_failed_val}"
        )


def _get_adapter(source_type: str, mapping_profile: str = "default"):
    if source_type == "csv" or source_type == "json":
        return CSVJSONAdapter(mapping_profile=mapping_profile)
    if source_type == "pdf":
        return PDFTextAdapter()
    if source_type == "ocr_image":
        return OCRImageAdapter()
    raise ValueError(f"Unknown source_type: {source_type}")


def _resolve_store(session, store_name: Optional[str]) -> Store:
    name = (store_name or "Unknown Store").strip()
    store = session.query(Store).filter_by(store_name=name).first()
    if not store:
        store = Store(store_name=name)
        session.add(store)
        session.flush()
    return store


def _resolve_customer(
    session,
    customer_name: Optional[str],
    customer_phone: Optional[str] = None,
) -> Optional[Customer]:
    """Resolve customer by normalized phone (preferred) or fall back to name."""
    phone_norm = None
    if customer_phone and str(customer_phone).strip():
        try:
            phone_norm = normalize_phone(customer_phone)
        except InvalidPhoneError:
            phone_norm = None

    if phone_norm:
        customer = session.query(Customer).filter_by(phone_normalized=phone_norm).first()
        if customer:
            if customer_name and customer_name.strip() and not customer.customer_name:
                customer.customer_name = customer_name.strip()
            customer.phone = str(customer_phone).strip()
            session.flush()
            return customer
        customer = Customer(
            customer_name=customer_name.strip() if customer_name else None,
            phone=str(customer_phone).strip(),
            phone_normalized=phone_norm,
        )
        session.add(customer)
        session.flush()
        return customer

    if not customer_name or not customer_name.strip():
        return None
    name = customer_name.strip()
    customer = session.query(Customer).filter_by(customer_name=name).first()
    if not customer:
        customer = Customer(customer_name=name)
        session.add(customer)
        session.flush()
    return customer


def _log_error(session, source_file_path: str, error_type: str, error_detail: str, raw_payload: Optional[dict] = None):
    err = IngestionError(
        source_file_path=source_file_path,
        error_type=error_type,
        error_detail=error_detail,
        raw_payload=raw_payload,
    )
    session.add(err)


def _check_price_outlier(session, product_id: str, unit_price: float, threshold_z: float) -> bool:
    """Compares against this product's historical unit prices. Returns
    True if this looks like an outlier. With too little history, we
    can't compute a meaningful z-score, so we don't flag (avoids false
    positives on brand-new products)."""
    history = (
        session.query(InvoiceLineItem.unit_price)
        .filter(InvoiceLineItem.product_id == product_id)
        .all()
    )
    prices = [float(p[0]) for p in history if p[0] is not None]
    if len(prices) < 5:
        return False
    mean = statistics.mean(prices)
    stdev = statistics.pstdev(prices)
    if stdev == 0:
        return False
    z = abs((unit_price - mean) / stdev)
    return z > threshold_z


def ingest_invoice(session, raw: RawInvoiceData, thresholds: dict) -> str:
    """Processes a single RawInvoiceData. Returns one of:
    'loaded', 'duplicate', 'failed_validation'.
    Raises nothing -- all failures are logged to ingestion_errors."""

    parsed_date = parse_date(raw.invoice_date)
    parsed_time = parse_time(raw.invoice_time) if raw.invoice_time else None

    # Pre-resolve store to check natural-key duplication (store_id is part
    # of the natural key).
    store = _resolve_store(session, raw.store_name)

    if parsed_date and is_duplicate_by_natural_key(session, store.store_id, raw.invoice_number, parsed_date):
        logger.info(f"Skipping duplicate invoice {raw.invoice_number} for store {store.store_name}")
        return "duplicate"

    # File-hash dedup only makes sense for one-invoice-per-file sources
    # (PDF/OCR scans). A CSV/JSON export legitimately contains many
    # invoices sharing the same source file, so file-hash matching there
    # would falsely flag every invoice after the first as a duplicate --
    # natural-key dedup (above) is the right check for those sources.
    h = None
    if raw.source_type in ("pdf", "ocr_image") and raw.source_file_path and os.path.exists(raw.source_file_path):
        h = file_hash(raw.source_file_path)
        if is_duplicate_by_file_hash(session, h):
            logger.info(f"Skipping duplicate file (hash match): {raw.source_file_path}")
            return "duplicate"

    # --- Validate line items first, since header validation depends on count/flags ---
    matcher = ProductMatcher(session)
    cleaned_line_items = []
    negative_qty_found = False
    negative_price_found = False
    line_item_errors = []

    for li in raw.line_items:
        qty = clean_number(li.quantity)
        price = clean_number(li.unit_price)
        discount_pct = clean_number(li.discount_pct) or 0.0
        tax_pct = clean_number(li.tax_pct)
        line_total = clean_number(li.line_total)

        display_name = li.product_name or li.sku or li.product_id or "(unknown)"
        errs = validate_line_item(li.product_name or display_name, qty, price)
        if errs:
            line_item_errors.append({"product_name": display_name, "errors": errs})
            continue  # skip this specific line item, don't kill the whole invoice

        if qty is not None and qty < 0:
            negative_qty_found = True
        if price is not None and price < 0:
            negative_price_found = True

        try:
            product, match_type = matcher.resolve_identity(
                product_id=getattr(li, "product_id", None),
                sku=getattr(li, "sku", None),
                product_name=li.product_name,
                category=li.category,
            )
        except ValueError as exc:
            line_item_errors.append({"product_name": display_name, "errors": [str(exc)]})
            continue

        if match_type == "ambiguous_new":
            _log_error(
                session,
                raw.source_file_path,
                "ambiguous_product_match",
                f"'{li.product_name}' matched no existing product with high confidence; created as new product '{product.product_name}' pending review.",
                raw_payload={"raw_name": li.product_name},
            )

        # Persist SKU / unit_cost from live POS payloads when provided
        if getattr(li, "sku", None) and not product.sku:
            product.sku = str(li.sku).strip()
        unit_cost = clean_number(getattr(li, "unit_cost", None)) if getattr(li, "unit_cost", None) else None
        if unit_cost is not None:
            product.unit_cost = unit_cost

        if price is not None:
            is_outlier = _check_price_outlier(
                session, product.product_id, price,
                thresholds["outlier_detection"]["price_zscore_threshold"],
            )
            if is_outlier:
                _log_error(
                    session,
                    raw.source_file_path,
                    "outlier_review",
                    f"Unit price {price} for product '{product.product_name}' is a statistical outlier vs history.",
                    raw_payload={"product_id": product.product_id, "unit_price": price},
                )

        if line_total is None and qty is not None and price is not None:
            line_total = round(qty * price * (1 - discount_pct / 100.0), 2)

        cleaned_line_items.append(
            dict(
                product_id=product.product_id,
                quantity=qty,
                unit_price=price,
                discount_pct=discount_pct,
                tax_pct=tax_pct,
                line_total=line_total,
                unit_cost=unit_cost,
            )
        )

    header_errors = validate_invoice(
        parsed_date=parsed_date,
        line_items_count=len(cleaned_line_items),
        negative_quantity_found=negative_qty_found,
        negative_price_found=negative_price_found,
    )

    if header_errors:
        _log_error(
            session,
            raw.source_file_path,
            "invoice_validation_failed",
            f"Invoice failed validation: {header_errors}",
            raw_payload=raw.model_dump(),
        )
        if line_item_errors:
            _log_error(
                session,
                raw.source_file_path,
                "line_item_validation_failed",
                f"{len(line_item_errors)} line item(s) failed validation and were skipped.",
                raw_payload={"line_item_errors": line_item_errors},
            )
        return "failed_validation"

    # --- Load: single transaction for invoice header + all line items ---
    customer = _resolve_customer(session, raw.customer_name, getattr(raw, "customer_phone", None))

    subtotal = sum(li["quantity"] * li["unit_price"] for li in cleaned_line_items if li["quantity"] and li["unit_price"])
    grand_total = sum(li["line_total"] for li in cleaned_line_items if li["line_total"])

    invoice = Invoice(
        store_id=store.store_id,
        customer_id=customer.customer_id if customer else None,
        invoice_number=raw.invoice_number,
        invoice_date=parsed_date,
        invoice_time=parsed_time,
        payment_method=raw.payment_method,
        subtotal=round(subtotal, 2),
        grand_total=round(grand_total, 2),
        source_type=raw.source_type,
        source_file_path=raw.source_file_path,
        file_hash=h,
        ingestion_confidence=raw.confidence,
    )
    session.add(invoice)
    session.flush()  # get invoice_id

    for li in cleaned_line_items:
        session.add(InvoiceLineItem(invoice_id=invoice.invoice_id, **li))

    if line_item_errors:
        _log_error(
            session,
            raw.source_file_path,
            "line_item_partial_skip",
            f"{len(line_item_errors)} line item(s) on an otherwise-valid invoice were skipped.",
            raw_payload={"line_item_errors": line_item_errors},
        )

    return "loaded"


def ingest_file(
    file_path: str,
    source_type: str,
    mapping_profile: str = "default",
    force_store_name: Optional[str] = None,
) -> IngestResult:
    """
    Ingest a file. When force_store_name is set (browser upload), every invoice
    is attributed to that store regardless of the Store column in the file.
    """
    result = IngestResult(file_path=file_path)
    thresholds = load_thresholds()

    try:
        adapter = _get_adapter(source_type, mapping_profile)
        raw_invoices = adapter.parse(file_path)
    except Exception as e:
        logger.error(f"Failed to parse {file_path}: {e}")
        session = get_session()
        try:
            _log_error(session, file_path, "parse_failure", str(e))
            session.commit()
        finally:
            session.close()
        result.errors.append(f"parse_failure: {e}")
        return result

    result.invoices_found = len(raw_invoices)

    for raw in raw_invoices:
        if force_store_name:
            raw.store_name = force_store_name
        session = get_session()
        try:
            outcome = ingest_invoice(session, raw, thresholds)
            session.commit()
            if outcome == "loaded":
                result.invoices_loaded += 1
            elif outcome == "duplicate":
                result.invoices_skipped_duplicate += 1
            elif outcome == "failed_validation":
                result.invoices_failed_validation += 1
        except Exception as e:
            session.rollback()
            logger.error(f"Unexpected error ingesting invoice from {file_path}: {e}")
            error_session = get_session()
            try:
                _log_error(error_session, file_path, "unexpected_error", str(e))
                error_session.commit()
            finally:
                error_session.close()
            result.errors.append(f"unexpected_error: {e}")
        finally:
            session.close()

    logger.info(
        f"{file_path}: found={result.invoices_found} loaded={result.invoices_loaded} "
        f"duplicates={result.invoices_skipped_duplicate} failed_validation={result.invoices_failed_validation}"
    )
    return result


def batch_ingest(folder_path: str, source_type: str, mapping_profile: str = "default") -> BatchIngestResult:
    batch_result = BatchIngestResult()
    files = sorted(os.listdir(folder_path))
    for fname in files:
        fpath = os.path.join(folder_path, fname)
        if not os.path.isfile(fpath):
            continue
        batch_result.files_processed += 1
        try:
            result = ingest_file(fpath, source_type, mapping_profile)
            batch_result.per_file_results.append(result)
            if result.errors:
                batch_result.files_failed += 1
            else:
                batch_result.files_succeeded += 1
        except Exception as e:
            logger.error(f"Fatal error processing {fpath}, continuing batch: {e}")
            batch_result.files_failed += 1
    logger.info(batch_result.summary())
    return batch_result
