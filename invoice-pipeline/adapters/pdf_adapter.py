"""
Adapter for text-based (not scanned) PDF invoices.

Uses pdfplumber to detect tables on each page and to pull header fields
(invoice number/date/store) from the surrounding text via simple
label-based regex matching. This is intentionally a heuristic, best-effort
parser -- real-world invoice layouts vary a lot. Anything it can't
confidently extract is left None and will be caught by the validation
stage downstream (never silently guessed).
"""
import re
from typing import List, Optional

import pdfplumber

from adapters.base import InvoiceAdapter, RawInvoiceData, RawLineItem

HEADER_PATTERNS = {
    "invoice_number": r"(?:invoice\s*(?:no|number|#)?)\s*[:\-]?\s*([A-Za-z0-9\-\/]+)",
    "invoice_date": r"(?:date)\s*[:\-]?\s*([0-9]{1,4}[\-\/][0-9]{1,2}[\-\/][0-9]{1,4})",
    "store_name": r"(?:store|branch|outlet)\s*[:\-]?\s*(.+)",
    "payment_method": r"(?:payment\s*method|paid\s*via)\s*[:\-]?\s*(\w+)",
}

# Header row synonyms used to identify which table column is which, since
# PDF-extracted tables give us raw string headers, not canonical fields.
COLUMN_SYNONYMS = {
    "product_name": ["item", "product", "description"],
    "quantity": ["qty", "quantity"],
    "unit_price": ["price", "rate", "unit price"],
    "discount_pct": ["discount", "disc%", "disc %"],
    "tax_pct": ["tax", "gst", "tax%"],
    "line_total": ["amount", "total", "line total"],
}


def _match_column(header_cell: Optional[str]) -> Optional[str]:
    if not header_cell:
        return None
    h = header_cell.strip().lower()
    for canonical, synonyms in COLUMN_SYNONYMS.items():
        if h in synonyms:
            return canonical
    return None


class PDFTextAdapter(InvoiceAdapter):
    def parse(self, file_path: str) -> List[RawInvoiceData]:
        full_text = []
        all_tables = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text.append(text)
                tables = page.extract_tables()
                all_tables.extend(tables)

        header_text = "\n".join(full_text)
        header_fields = self._extract_header_fields(header_text)
        line_items = self._extract_line_items(all_tables)

        return [
            RawInvoiceData(
                store_name=header_fields.get("store_name"),
                invoice_number=header_fields.get("invoice_number"),
                invoice_date=header_fields.get("invoice_date"),
                invoice_time=None,
                customer_name=None,
                payment_method=header_fields.get("payment_method"),
                line_items=line_items,
                source_type="pdf",
                source_file_path=file_path,
                confidence=None,
            )
        ]

    def _extract_header_fields(self, text: str) -> dict:
        results = {}
        for field, pattern in HEADER_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                results[field] = match.group(1).strip()
        return results

    def _extract_line_items(self, tables: List[list]) -> List[RawLineItem]:
        line_items = []
        for table in tables:
            if not table or len(table) < 2:
                continue
            header_row = table[0]
            col_map = {i: _match_column(cell) for i, cell in enumerate(header_row)}

            # Only treat this as a line-item table if we recognized at
            # least product_name + one numeric-ish column.
            recognized = set(v for v in col_map.values() if v)
            if "product_name" not in recognized:
                continue

            for row in table[1:]:
                row_data = {}
                for i, cell in enumerate(row):
                    canonical = col_map.get(i)
                    if canonical:
                        row_data[canonical] = cell
                if not row_data.get("product_name"):
                    continue
                line_items.append(
                    RawLineItem(
                        product_name=row_data.get("product_name", "") or "",
                        category=None,
                        quantity=row_data.get("quantity"),
                        unit_price=row_data.get("unit_price"),
                        discount_pct=row_data.get("discount_pct"),
                        tax_pct=row_data.get("tax_pct"),
                        line_total=row_data.get("line_total"),
                    )
                )
        return line_items
