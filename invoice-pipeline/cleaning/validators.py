"""
Validates a RawInvoiceData object (after date/number parsing has been
attempted) before it's allowed anywhere near the database. Returns a
list of validation error strings -- empty list means valid.

Design principle: validation NEVER silently drops or "fixes" data.
It only classifies it as valid/invalid; the caller decides what to do
(usually: log to ingestion_errors and skip DB insert for that invoice).
"""
from typing import List, Optional
from datetime import date

from pipeline.config_loader import load_thresholds


def validate_invoice(
    parsed_date: Optional[date],
    line_items_count: int,
    negative_quantity_found: bool,
    negative_price_found: bool,
) -> List[str]:
    thresholds = load_thresholds()["validation"]
    errors = []

    if parsed_date is None:
        errors.append("missing_or_unparseable_invoice_date")

    if line_items_count < thresholds["min_line_items_per_invoice"]:
        errors.append("no_line_items")

    if negative_quantity_found and not thresholds["allow_negative_quantity"]:
        errors.append("negative_quantity_not_allowed")

    if negative_price_found and not thresholds["allow_negative_price"]:
        errors.append("negative_price_not_allowed")

    return errors


def validate_line_item(product_name: str, quantity: Optional[float], unit_price: Optional[float]) -> List[str]:
    thresholds = load_thresholds()
    errors = []

    if not product_name or not product_name.strip():
        errors.append("missing_product_name")

    if quantity is None:
        errors.append("missing_or_unparseable_quantity")
    elif quantity > thresholds["outlier_detection"]["quantity_max_sane"]:
        errors.append("quantity_exceeds_sane_maximum")

    if unit_price is None:
        errors.append("missing_or_unparseable_unit_price")

    return errors
