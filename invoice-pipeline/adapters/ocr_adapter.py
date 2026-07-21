"""
Adapter for scanned/photographed invoice images (or image-only PDFs).

Runs Tesseract OCR, computes an average word-confidence score, and applies
the same header-pattern + line-parsing heuristics as the PDF adapter
(OCR output is just noisier text, so we reuse regex-based extraction
rather than table detection, which is unreliable on OCR text).

Every invoice produced here carries a `confidence` score. The pipeline's
validation stage is responsible for deciding what to do with low-confidence
invoices (default: flag for manual review, still ingest) -- this adapter's
only job is to extract data and report how confident it is.
"""
import re
from typing import List, Optional

import pytesseract
from PIL import Image

from adapters.base import InvoiceAdapter, RawInvoiceData, RawLineItem
from adapters.pdf_adapter import HEADER_PATTERNS

# A line-item line in OCR text tends to look like:
#   "Basmati Rice 5kg   2   450.00   900.00"
# i.e. a product description followed by 2+ numeric tokens.
LINE_ITEM_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9 ,\.\-/%]{2,40}?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<price>\d+(?:\.\d+)?)\s+"
    r"(?P<total>\d+(?:\.\d+)?)\s*$"
)


class OCRImageAdapter(InvoiceAdapter):
    def parse(self, file_path: str) -> List[RawInvoiceData]:
        image = Image.open(file_path)

        try:
            ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractNotFoundError as exc:
            raise RuntimeError(
                "Tesseract OCR is not installed or not on PATH. "
                "Install it (e.g. `brew install tesseract`) to parse scanned invoices."
            ) from exc
        confidences = [int(c) for c in ocr_data.get("conf", []) if str(c).strip() not in ("", "-1")]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        text = pytesseract.image_to_string(image)

        header_fields = self._extract_header_fields(text)
        line_items = self._extract_line_items(text)

        return [
            RawInvoiceData(
                store_name=header_fields.get("store_name"),
                invoice_number=header_fields.get("invoice_number"),
                invoice_date=header_fields.get("invoice_date"),
                invoice_time=None,
                customer_name=None,
                payment_method=header_fields.get("payment_method"),
                line_items=line_items,
                source_type="ocr_image",
                source_file_path=file_path,
                confidence=round(avg_confidence, 2),
            )
        ]

    def _extract_header_fields(self, text: str) -> dict:
        results = {}
        for field, pattern in HEADER_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                results[field] = match.group(1).strip()
        return results

    def _extract_line_items(self, text: str) -> List[RawLineItem]:
        line_items = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = LINE_ITEM_PATTERN.match(line)
            if not match:
                continue
            line_items.append(
                RawLineItem(
                    product_name=match.group("name").strip(),
                    category=None,
                    quantity=match.group("qty"),
                    unit_price=match.group("price"),
                    discount_pct=None,
                    tax_pct=None,
                    line_total=match.group("total"),
                )
            )
        return line_items
