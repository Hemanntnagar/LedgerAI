"""Unit tests for ingestion adapters, using fixture files. These test
parsing/extraction only -- no database involved."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from adapters.csv_json_adapter import CSVJSONAdapter
from adapters.pdf_adapter import PDFTextAdapter
from adapters.ocr_adapter import OCRImageAdapter

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_csv_adapter_groups_line_items_into_invoices():
    adapter = CSVJSONAdapter()
    invoices = adapter.parse(os.path.join(FIXTURES, "sample_invoices.csv"))
    invoice_numbers = [inv.invoice_number for inv in invoices]
    assert "INV-1001" in invoice_numbers
    inv_1001 = next(inv for inv in invoices if inv.invoice_number == "INV-1001")
    assert len(inv_1001.line_items) == 3
    assert inv_1001.store_name == "Downtown Mart"
    assert inv_1001.invoice_date == "2026-05-01"


def test_csv_adapter_maps_varied_column_names():
    adapter = CSVJSONAdapter()
    invoices = adapter.parse(os.path.join(FIXTURES, "sample_invoices.csv"))
    inv_1001 = next(inv for inv in invoices if inv.invoice_number == "INV-1001")
    li = inv_1001.line_items[0]
    assert li.product_name == "Basmati Rice 5kg"
    assert li.quantity == "2"
    assert li.unit_price == "450"


def test_csv_adapter_unknown_profile_raises():
    with pytest.raises(ValueError):
        CSVJSONAdapter(mapping_profile="nonexistent_profile")


def test_pdf_adapter_extracts_header_and_line_items():
    adapter = PDFTextAdapter()
    invoices = adapter.parse(os.path.join(FIXTURES, "sample_invoice.pdf"))
    assert len(invoices) == 1
    inv = invoices[0]
    assert inv.invoice_number == "INV-2001"
    assert len(inv.line_items) == 3
    product_names = [li.product_name for li in inv.line_items]
    assert "Basmati Rice 5kg" in product_names


def test_ocr_adapter_extracts_with_confidence_score():
    try:
        import shutil
        if shutil.which("tesseract") is None:
            pytest.skip("tesseract binary not installed (OCR optional)")
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception:
        pytest.skip("tesseract not available (OCR optional)")

    adapter = OCRImageAdapter()
    invoices = adapter.parse(os.path.join(FIXTURES, "sample_invoice_scan.png"))
    assert len(invoices) == 1
    inv = invoices[0]
    assert inv.confidence is not None
    assert inv.confidence > 0
    assert len(inv.line_items) >= 1
