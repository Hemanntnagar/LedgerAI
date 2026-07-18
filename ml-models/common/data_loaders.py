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
from db.models import (  # noqa: E402
    Base,
    Store,
    Product,
    Invoice,
    InvoiceLineItem,
    Customer,
    FestivalEvent,
)
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


def load_product_meta(session) -> pd.DataFrame:
    """product_id, name, category, unit_cost."""
    rows = session.query(
        Product.product_id, Product.product_name, Product.category, Product.unit_cost
    ).all()
    df = pd.DataFrame(rows, columns=["product_id", "product_name", "category", "unit_cost"])
    if not df.empty:
        df["unit_cost"] = df["unit_cost"].astype(float)
    return df


def has_customer_data(session, store_id=None) -> bool:
    q = session.query(Invoice).filter(Invoice.customer_id.isnot(None))
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    return q.first() is not None


def load_customer_invoices(session, store_id=None) -> pd.DataFrame:
    """One row per invoice with customer phone linkage (for RFM / NBO)."""
    q = (
        session.query(
            Invoice.invoice_id,
            Invoice.store_id,
            Invoice.customer_id,
            Customer.phone_normalized,
            Invoice.invoice_date,
            Invoice.grand_total,
        )
        .join(Customer, Customer.customer_id == Invoice.customer_id)
        .filter(Invoice.customer_id.isnot(None))
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    rows = q.all()
    df = pd.DataFrame(
        rows,
        columns=[
            "invoice_id",
            "store_id",
            "customer_id",
            "phone_normalized",
            "invoice_date",
            "grand_total",
        ],
    )
    if not df.empty:
        df["invoice_date"] = pd.to_datetime(df["invoice_date"])
        df["grand_total"] = df["grand_total"].astype(float)
    return df


def load_customer_baskets(session, store_id=None) -> pd.DataFrame:
    """invoice_id, customer_id, store_id, product_id for phone-linked invoices."""
    q = (
        session.query(
            Invoice.invoice_id,
            Invoice.customer_id,
            Invoice.store_id,
            InvoiceLineItem.product_id,
        )
        .join(InvoiceLineItem, InvoiceLineItem.invoice_id == Invoice.invoice_id)
        .filter(Invoice.customer_id.isnot(None))
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    rows = q.all()
    return pd.DataFrame(rows, columns=["invoice_id", "customer_id", "store_id", "product_id"])


def load_festivals(session, region: str = "india") -> pd.DataFrame:
    rows = (
        session.query(FestivalEvent)
        .filter(FestivalEvent.region == region)
        .order_by(FestivalEvent.start_date)
        .all()
    )
    if not rows:
        return pd.DataFrame(
            columns=["festival_id", "name", "year", "start_date", "end_date", "category", "notes"]
        )
    return pd.DataFrame(
        [
            {
                "festival_id": f.festival_id,
                "name": f.name,
                "year": f.year,
                "start_date": f.start_date,
                "end_date": f.end_date,
                "category": f.category,
                "notes": f.notes,
            }
            for f in rows
        ]
    )


def load_store_daily_revenue(session, store_id=None) -> pd.DataFrame:
    q = (
        session.query(
            Invoice.store_id,
            Invoice.invoice_date.label("date"),
            func.sum(Invoice.grand_total).label("revenue"),
            func.count(Invoice.invoice_id).label("txn_count"),
        )
        .group_by(Invoice.store_id, Invoice.invoice_date)
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    rows = q.all()
    df = pd.DataFrame(rows, columns=["store_id", "date", "revenue", "txn_count"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["revenue"] = df["revenue"].astype(float)
        df["txn_count"] = df["txn_count"].astype(int)
    return df


def resolve_store_id(session, store_id=None, store_name=None):
    """Resolve optional store_name → store_id; return store_id or None (all stores)."""
    if store_id:
        return store_id
    if store_name:
        store = session.query(Store).filter_by(store_name=store_name).first()
        if not store:
            raise ValueError(f"Store not found: {store_name}")
        return store.store_id
    return None


def list_stores(session, store_id=None):
    q = session.query(Store)
    if store_id:
        q = q.filter(Store.store_id == store_id)
    return q.all()
