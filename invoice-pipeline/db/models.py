"""
SQLAlchemy models for the invoice ingestion pipeline.

Designed against SQLite for local development/demo, but uses only
standard column types so switching DB_URL to a PostgreSQL connection
string in config.py is a drop-in change (see db/session.py).
"""
import uuid
from datetime import datetime, date, time as dtime

from sqlalchemy import (
    Column, String, Numeric, Integer, Date, Time, DateTime, ForeignKey,
    Text, JSON, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Store(Base):
    __tablename__ = "stores"

    store_id = Column(String, primary_key=True, default=gen_uuid)
    store_name = Column(Text, nullable=False)
    location = Column(Text, nullable=True)
    region = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="store")

    __table_args__ = (UniqueConstraint("store_name", name="uq_store_name"),)


class Product(Base):
    __tablename__ = "products"

    product_id = Column(String, primary_key=True, default=gen_uuid)
    product_name = Column(Text, nullable=False)
    category = Column(Text, nullable=True)
    sku = Column(Text, nullable=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    aliases = relationship("ProductAlias", back_populates="product")


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    alias_id = Column(Integer, primary_key=True, autoincrement=True)
    raw_name = Column(Text, nullable=False)
    product_id = Column(String, ForeignKey("products.product_id"), nullable=False)

    product = relationship("Product", back_populates="aliases")

    __table_args__ = (UniqueConstraint("raw_name", name="uq_alias_raw_name"),)


class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(String, primary_key=True, default=gen_uuid)
    customer_name = Column(Text, nullable=True)
    phone = Column(Text, nullable=True)
    email = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    invoice_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    customer_id = Column(String, ForeignKey("customers.customer_id"), nullable=True)
    invoice_number = Column(Text, nullable=True)
    invoice_date = Column(Date, nullable=False)
    invoice_time = Column(Time, nullable=True)
    payment_method = Column(Text, nullable=True)
    subtotal = Column(Numeric(12, 2), nullable=True)
    tax_total = Column(Numeric(12, 2), nullable=True)
    discount_total = Column(Numeric(12, 2), nullable=True)
    grand_total = Column(Numeric(12, 2), nullable=True)
    source_type = Column(Text, nullable=False)  # 'csv' | 'pdf' | 'ocr_image'
    source_file_path = Column(Text, nullable=True)
    file_hash = Column(Text, nullable=True)  # for dedup
    ingested_at = Column(DateTime, default=datetime.utcnow)
    ingestion_confidence = Column(Numeric(5, 2), nullable=True)

    store = relationship("Store", back_populates="invoices")
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "invoice_number", "invoice_date", name="uq_invoice_natural_key"),
    )


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    line_item_id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(String, ForeignKey("invoices.invoice_id"), nullable=False)
    product_id = Column(String, ForeignKey("products.product_id"), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    discount_pct = Column(Numeric(5, 2), nullable=True, default=0)
    tax_pct = Column(Numeric(5, 2), nullable=True)
    line_total = Column(Numeric(12, 2), nullable=False)

    invoice = relationship("Invoice", back_populates="line_items")
    product = relationship("Product")


class IngestionError(Base):
    __tablename__ = "ingestion_errors"

    error_id = Column(Integer, primary_key=True, autoincrement=True)
    source_file_path = Column(Text, nullable=True)
    error_type = Column(Text, nullable=False)
    error_detail = Column(Text, nullable=False)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
