"""
Duplicate-invoice detection.

Two strategies, both checked:
  1. Natural key: (store_id, invoice_number, invoice_date) already exists
  2. File hash: the exact same source file was already ingested
     (catches re-uploads of the same file even before parsing)
"""
import hashlib
from typing import Optional

from db.models import Invoice


def file_hash(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def is_duplicate_by_natural_key(session, store_id: str, invoice_number: Optional[str], invoice_date) -> bool:
    if not invoice_number:
        return False  # can't dedup on natural key without an invoice number
    existing = (
        session.query(Invoice)
        .filter_by(store_id=store_id, invoice_number=invoice_number, invoice_date=invoice_date)
        .first()
    )
    return existing is not None


def is_duplicate_by_file_hash(session, hash_value: str) -> bool:
    existing = session.query(Invoice).filter_by(file_hash=hash_value).first()
    return existing is not None
