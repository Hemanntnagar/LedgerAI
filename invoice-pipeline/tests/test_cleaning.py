"""Unit tests for cleaning/normalization functions -- pure functions,
no database needed."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from cleaning.normalizer import clean_number
from cleaning.date_parser import parse_date, parse_time
from cleaning.validators import validate_invoice, validate_line_item


def test_clean_number_handles_currency_symbols():
    assert clean_number("₹450.00") == 450.00
    assert clean_number("$1,200.50") == 1200.50
    assert clean_number("  99 ") == 99.0


def test_clean_number_handles_parentheses_as_negative():
    assert clean_number("(120.00)") == -120.00


def test_clean_number_returns_none_for_garbage():
    assert clean_number("not a number") is None
    assert clean_number("") is None
    assert clean_number(None) is None


def test_clean_number_passthrough_numeric_types():
    assert clean_number(42) == 42.0
    assert clean_number(3.5) == 3.5


def test_parse_date_handles_multiple_formats():
    assert parse_date("2026-05-01") == date(2026, 5, 1)
    assert parse_date("01-05-2026") == date(2026, 5, 1)
    # Note: "05/01/2026" is genuinely ambiguous between DD/MM and MM/DD.
    # The parser tries DD/MM/YYYY first (more common outside the US), so
    # it resolves to Jan 5, not May 1. We test with an unambiguous date
    # (day > 12) to confirm the MM/DD fallback format is reachable at all.
    assert parse_date("12/25/2026") == date(2026, 12, 25)  # unambiguous mm/dd/yyyy


def test_parse_date_returns_none_for_unparseable():
    assert parse_date("not a date") is None
    assert parse_date(None) is None
    assert parse_date("") is None


def test_parse_time_handles_common_formats():
    assert parse_time("14:30") is not None
    assert parse_time("02:30 PM") is not None
    assert parse_time("garbage") is None


def test_validate_invoice_flags_missing_date():
    errors = validate_invoice(
        parsed_date=None, line_items_count=2,
        negative_quantity_found=False, negative_price_found=False
    )
    assert "missing_or_unparseable_invoice_date" in errors


def test_validate_invoice_flags_no_line_items():
    errors = validate_invoice(
        parsed_date=date(2026, 1, 1), line_items_count=0,
        negative_quantity_found=False, negative_price_found=False
    )
    assert "no_line_items" in errors


def test_validate_invoice_flags_negative_quantity_by_default():
    errors = validate_invoice(
        parsed_date=date(2026, 1, 1), line_items_count=1,
        negative_quantity_found=True, negative_price_found=False
    )
    assert "negative_quantity_not_allowed" in errors


def test_validate_invoice_passes_clean_data():
    errors = validate_invoice(
        parsed_date=date(2026, 1, 1), line_items_count=2,
        negative_quantity_found=False, negative_price_found=False
    )
    assert errors == []


def test_validate_line_item_flags_missing_product_name():
    errors = validate_line_item(product_name="", quantity=1, unit_price=10)
    assert "missing_product_name" in errors


def test_validate_line_item_flags_missing_quantity_and_price():
    errors = validate_line_item(product_name="Rice", quantity=None, unit_price=None)
    assert "missing_or_unparseable_quantity" in errors
    assert "missing_or_unparseable_unit_price" in errors


def test_validate_line_item_passes_clean_data():
    errors = validate_line_item(product_name="Rice", quantity=2, unit_price=100)
    assert errors == []
