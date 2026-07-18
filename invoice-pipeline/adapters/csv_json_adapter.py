"""
Adapter for structured POS/ERP exports (CSV or JSON).

Handles varying column names via config/field_mappings.yaml so a new
export format from a different POS system can be supported by adding
a config block, not writing new code.

Expected input shape: one row per invoice LINE ITEM (the normal shape
of a POS export), grouped into invoices by (invoice_number, invoice_date,
store_name) when those are present, or treated as one invoice per row
otherwise (least-common-denominator fallback).
"""
import csv
import json
import os
from typing import List, Dict, Optional

from adapters.base import InvoiceAdapter, RawInvoiceData, RawLineItem
from pipeline.config_loader import load_field_mappings


def _build_reverse_map(mapping_profile: dict) -> Dict[str, str]:
    """Turn {canonical_field: [raw_name, raw_name, ...]} into
    {raw_name_lowercased: canonical_field} for fast lookup."""
    reverse = {}
    for canonical, raw_names in mapping_profile.items():
        for raw_name in raw_names:
            reverse[raw_name.strip().lower()] = canonical
    return reverse


class CSVJSONAdapter(InvoiceAdapter):
    def __init__(self, mapping_profile: str = "default"):
        mappings = load_field_mappings()
        if mapping_profile not in mappings:
            raise ValueError(
                f"Unknown field mapping profile '{mapping_profile}'. "
                f"Available: {list(mappings.keys())}"
            )
        self.reverse_map = _build_reverse_map(mappings[mapping_profile])

    def _normalize_row(self, row: dict) -> dict:
        """Rename raw column keys to canonical field names using the
        configured mapping. Unmapped columns are dropped (not needed
        downstream, but kept out of the way)."""
        normalized = {}
        for raw_key, value in row.items():
            if raw_key is None:
                continue
            canonical = self.reverse_map.get(raw_key.strip().lower())
            if canonical:
                normalized[canonical] = value
        return normalized

    def parse(self, file_path: str) -> List[RawInvoiceData]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".csv":
            rows = self._read_csv(file_path)
        elif ext == ".json":
            rows = self._read_json(file_path)
        else:
            raise ValueError(f"CSVJSONAdapter cannot handle file type: {ext}")

        normalized_rows = [self._normalize_row(r) for r in rows]
        return self._group_into_invoices(normalized_rows, file_path)

    def _read_csv(self, file_path: str) -> List[dict]:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def _read_json(self, file_path: str) -> List[dict]:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        # Accept either a flat list of line-item dicts, or {"invoices": [...]}
        if isinstance(data, dict) and "invoices" in data:
            rows = []
            for inv in data["invoices"]:
                header = {k: v for k, v in inv.items() if k != "line_items"}
                for li in inv.get("line_items", []):
                    rows.append({**header, **li})
            return rows
        if isinstance(data, list):
            return data
        raise ValueError("Unrecognized JSON invoice structure")

    def _group_into_invoices(self, rows: List[dict], file_path: str) -> List[RawInvoiceData]:
        groups: Dict[tuple, List[dict]] = {}
        order: List[tuple] = []
        for row in rows:
            key = (
                row.get("invoice_number") or "UNKNOWN",
                row.get("invoice_date") or "UNKNOWN",
                row.get("store_name") or "UNKNOWN",
            )
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        invoices = []
        for key in order:
            group_rows = groups[key]
            header = group_rows[0]
            line_items = [
                RawLineItem(
                    product_name=r.get("product_name", "") or "",
                    category=r.get("category"),
                    quantity=r.get("quantity"),
                    unit_price=r.get("unit_price"),
                    discount_pct=r.get("discount_pct"),
                    tax_pct=r.get("tax_pct"),
                    line_total=r.get("line_total"),
                )
                for r in group_rows
            ]
            invoices.append(
                RawInvoiceData(
                    store_name=header.get("store_name"),
                    invoice_number=header.get("invoice_number"),
                    invoice_date=header.get("invoice_date"),
                    invoice_time=header.get("invoice_time"),
                    customer_name=header.get("customer_name"),
                    payment_method=header.get("payment_method"),
                    line_items=line_items,
                    source_type="csv",
                    source_file_path=file_path,
                    confidence=None,
                )
            )
        return invoices
