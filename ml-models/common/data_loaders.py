"""
Shared database access for all ML modules. Reuses the Phase 1
invoice-pipeline's models/engine (same pattern as the Phase 2 dashboard).
"""
import os
import sys

PIPELINE_ROOT = os.environ.get(
    "INVOICE_PIPELINE_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "invoice-pipeline"),
)
sys.path.insert(0, PIPELINE_ROOT)

from db.session import get_session, engine  # noqa: E402
from db.models import Base, Store, Product, Invoice, InvoiceLineItem, Customer  # noqa: E402
import pandas as pd
from sqlalchemy import func


def load_daily_product_sales(session, store_id=None, product_id=None) -> pd.DataFrame:
    """Returns one row per (store_id, product_id, date) with units and revenue.
    This is the base time series everything in forecasting/elasticity builds on."""
    q = (
        session.query(
            Invoice.store_id,
            InvoiceLineItem.product_id,
            Invoice.invoice_date.label("date"),
            func.sum(InvoiceLineItem.quantity).label("units"),
            func.sum(InvoiceLineItem.line_total).label("revenue"),
            func.avg(InvoiceLineItem.unit_price).label("avg_unit_price"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.invoice_id == Invoice.invoice_id)
        .group_by(Invoice.store_id, InvoiceLineItem.product_id, Invoice.invoice_date)
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    if product_id:
        q = q.filter(InvoiceLineItem.product_id == product_id)

    rows = q.all()
    df = pd.DataFrame(rows, columns=["store_id", "product_id", "date", "units", "revenue", "avg_unit_price"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["units"] = df["units"].astype(float)
        df["revenue"] = df["revenue"].astype(float)
        df["avg_unit_price"] = df["avg_unit_price"].astype(float)
    return df


def load_baskets(session, store_id=None) -> pd.DataFrame:
    """Returns one row per invoice_id with a list of product_ids in that basket."""
    q = (
        session.query(InvoiceLineItem.invoice_id, InvoiceLineItem.product_id, Invoice.store_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    rows = q.all()
    df = pd.DataFrame(rows, columns=["invoice_id", "product_id", "store_id"])
    return df


def load_product_names(session) -> dict:
    return {p.product_id: p.product_name for p in session.query(Product).all()}


def has_customer_data(session) -> bool:
    return session.query(Invoice).filter(Invoice.customer_id.isnot(None)).first() is not None
