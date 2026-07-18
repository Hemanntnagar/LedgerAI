"""
Reuses the exact same database and models as the Phase 1 ingestion
pipeline -- this dashboard is a read-only layer on top of that data.
"""
import os
import sys

# Make the sibling invoice-pipeline project importable so we reuse its
# SQLAlchemy models/engine instead of duplicating the schema.
PIPELINE_ROOT = os.environ.get(
    "INVOICE_PIPELINE_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "invoice-pipeline"),
)
sys.path.insert(0, PIPELINE_ROOT)

from db.session import get_session, engine  # noqa: E402
from db.models import Store, Product, Invoice, InvoiceLineItem, Customer  # noqa: E402
