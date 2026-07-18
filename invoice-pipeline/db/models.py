"""
SQLAlchemy models for the invoice ingestion pipeline.

Designed against SQLite for local development/demo, but uses only
standard column types so switching DB_URL to a PostgreSQL connection
string in config.py is a drop-in change (see db/session.py).

Step 0 extensions: hardened customers (unique normalized phone), offers,
subscriptions, loyalty, festival calendar, insights, and report snapshots.
Step 3: daily_revenue_facts + monthly_revenue_facts for D/W/M analytics.
Step 9: job_runs + monitoring_alerts for automation.
"""
import uuid
from datetime import datetime, date, time as dtime

from sqlalchemy import (
    Column, String, Numeric, Integer, Date, Time, DateTime, ForeignKey,
    Text, JSON, UniqueConstraint, Index, Boolean,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Core entities (Phase 1)
# ---------------------------------------------------------------------------

class Store(Base):
    __tablename__ = "stores"

    store_id = Column(String, primary_key=True, default=gen_uuid)
    store_name = Column(Text, nullable=False)
    location = Column(Text, nullable=True)
    region = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="store")
    subscription = relationship("Subscription", back_populates="store", uselist=False)

    __table_args__ = (UniqueConstraint("store_name", name="uq_store_name"),)


class Product(Base):
    __tablename__ = "products"

    product_id = Column(String, primary_key=True, default=gen_uuid)
    product_name = Column(Text, nullable=False)
    category = Column(Text, nullable=True)
    sku = Column(Text, nullable=True, unique=True)
    # Optional margin / inventory proxies (contract: optional but valuable)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    stock_qty = Column(Numeric(12, 2), nullable=True)
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
    """Identity is phone_normalized (E.164). Name/email are soft attributes."""
    __tablename__ = "customers"

    customer_id = Column(String, primary_key=True, default=gen_uuid)
    customer_name = Column(Text, nullable=True)
    phone = Column(Text, nullable=True)  # raw as received
    phone_normalized = Column(String, nullable=True)  # +91XXXXXXXXXX
    email = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="customer")
    loyalty_accounts = relationship("LoyaltyAccount", back_populates="customer")

    __table_args__ = (
        UniqueConstraint("phone_normalized", name="uq_customer_phone_normalized"),
        Index("ix_customers_phone_normalized", "phone_normalized"),
    )


class Offer(Base):
    """Store (or chain-wide) promo / offer definition."""
    __tablename__ = "offers"

    offer_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=True)
    offer_code = Column(Text, nullable=False)
    offer_name = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    discount_pct = Column(Numeric(5, 2), nullable=True)
    starts_on = Column(Date, nullable=True)
    ends_on = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("store_id", "offer_code", name="uq_offer_store_code"),
        Index("ix_offers_code", "offer_code"),
    )


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
    # 'csv' | 'json' | 'pdf' | 'ocr_image' | 'api'
    source_type = Column(Text, nullable=False)
    source_file_path = Column(Text, nullable=True)
    file_hash = Column(Text, nullable=True)  # for dedup
    ingested_at = Column(DateTime, default=datetime.utcnow)
    ingestion_confidence = Column(Numeric(5, 2), nullable=True)

    store = relationship("Store", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")
    line_items = relationship("InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan")
    invoice_offers = relationship("InvoiceOffer", back_populates="invoice", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "invoice_number", "invoice_date", name="uq_invoice_natural_key"),
        Index("ix_invoices_store_date", "store_id", "invoice_date"),
        Index("ix_invoices_customer", "customer_id"),
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
    offer_id = Column(String, ForeignKey("offers.offer_id"), nullable=True)
    unit_cost = Column(Numeric(12, 2), nullable=True)

    invoice = relationship("Invoice", back_populates="line_items")
    product = relationship("Product")
    offer = relationship("Offer")


class InvoiceOffer(Base):
    """Invoice-level promo linkage (basket-wide offers)."""
    __tablename__ = "invoice_offers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(String, ForeignKey("invoices.invoice_id"), nullable=False)
    offer_id = Column(String, ForeignKey("offers.offer_id"), nullable=False)

    invoice = relationship("Invoice", back_populates="invoice_offers")
    offer = relationship("Offer")

    __table_args__ = (
        UniqueConstraint("invoice_id", "offer_id", name="uq_invoice_offer"),
    )


class IngestionError(Base):
    __tablename__ = "ingestion_errors"

    error_id = Column(Integer, primary_key=True, autoincrement=True)
    source_file_path = Column(Text, nullable=True)
    error_type = Column(Text, nullable=False)
    error_detail = Column(Text, nullable=False)
    raw_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Multi-tenant subscriptions (Step 2)
# ---------------------------------------------------------------------------

class SubscriptionPlan(Base):
    """Catalog of sellable plans (Basic/Pro/…). Quotas live here; subscriptions copy them."""
    __tablename__ = "subscription_plans"

    plan_code = Column(String, primary_key=True)  # free | basic | pro
    display_name = Column(Text, nullable=False)
    monthly_report_quota = Column(Integer, nullable=False, default=1)
    max_stores = Column(Integer, nullable=False, default=1)
    ml_refresh_hours = Column(Integer, nullable=False, default=168)  # min hours between ML runs
    require_customer_phone = Column(Boolean, nullable=False, default=False)
    features_json = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)


class Subscription(Base):
    __tablename__ = "subscriptions"

    subscription_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    plan = Column(Text, nullable=False, default="free")  # free | basic | pro
    status = Column(Text, nullable=False, default="active")  # active | past_due | cancelled
    api_key_hash = Column(Text, nullable=True)
    api_key_prefix = Column(String(16), nullable=True)
    monthly_report_quota = Column(Integer, nullable=False, default=1)
    reports_used_this_period = Column(Integer, nullable=False, default=0)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    ml_refresh_hours = Column(Integer, nullable=False, default=168)
    require_customer_phone = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    store = relationship("Store", back_populates="subscription")

    __table_args__ = (
        UniqueConstraint("store_id", name="uq_subscription_store"),
        Index("ix_subscriptions_status", "status"),
    )


class StoreUser(Base):
    """Dashboard login for a store (one or more users per store)."""
    __tablename__ = "store_users"

    user_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    username = Column(String, nullable=False)
    password_hash = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    store = relationship("Store")
    sessions = relationship("StoreSession", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("username", name="uq_store_user_username"),
        Index("ix_store_users_store", "store_id"),
    )


class StoreSession(Base):
    """Opaque session token for store dashboard auth (hashed at rest)."""
    __tablename__ = "store_sessions"

    session_id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("store_users.user_id"), nullable=False)
    token_hash = Column(Text, nullable=False)
    token_prefix = Column(String(16), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime, nullable=True)

    user = relationship("StoreUser", back_populates="sessions")

    __table_args__ = (
        Index("ix_store_sessions_prefix", "token_prefix"),
    )


# ---------------------------------------------------------------------------
# Phone-based loyalty (Step 4 logic; schema in Step 0)
# ---------------------------------------------------------------------------

class LoyaltyAccount(Base):
    __tablename__ = "loyalty_accounts"

    loyalty_account_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    customer_id = Column(String, ForeignKey("customers.customer_id"), nullable=False)
    points_balance = Column(Integer, nullable=False, default=0)
    tier = Column(Text, nullable=False, default="bronze")  # bronze | silver | gold
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="loyalty_accounts")
    transactions = relationship("LoyaltyTransaction", back_populates="account", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("store_id", "customer_id", name="uq_loyalty_store_customer"),
    )


class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"

    txn_id = Column(String, primary_key=True, default=gen_uuid)
    loyalty_account_id = Column(
        String, ForeignKey("loyalty_accounts.loyalty_account_id"), nullable=False
    )
    invoice_id = Column(String, ForeignKey("invoices.invoice_id"), nullable=True)
    points_delta = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False)  # earn | redeem | adjust | expire
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("LoyaltyAccount", back_populates="transactions")


# ---------------------------------------------------------------------------
# Indian festival calendar (Step 5 ML; schema + seed in Step 0)
# ---------------------------------------------------------------------------

class FestivalEvent(Base):
    __tablename__ = "festival_events"

    festival_id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(Text, nullable=False)
    year = Column(Integer, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    region = Column(Text, nullable=False, default="india")
    category = Column(Text, nullable=True)  # religious | national | regional | seasonal
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("name", "year", "region", name="uq_festival_name_year_region"),
        Index("ix_festival_dates", "start_date", "end_date"),
    )


# ---------------------------------------------------------------------------
# Insight engine + monthly reports (Steps 6–7; schema in Step 0)
# ---------------------------------------------------------------------------

class Insight(Base):
    """Ranked action produced by the deterministic insight engine."""
    __tablename__ = "insights"

    insight_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    action_type = Column(Text, nullable=False)
    priority = Column(Text, nullable=False)  # high | medium | low
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    impact_estimate = Column(Numeric(14, 2), nullable=True)  # rupees where possible
    confidence = Column(Numeric(5, 4), nullable=True)
    urgency = Column(Numeric(5, 4), nullable=True)
    score = Column(Numeric(14, 4), nullable=True)
    evidence_json = Column(JSON, nullable=True)
    source_models = Column(JSON, nullable=True)
    status = Column(Text, nullable=False, default="new")  # new|accepted|dismissed|done
    feedback = Column(Text, nullable=True)  # useful | not_useful | null
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_insights_store_status", "store_id", "status"),
        Index("ix_insights_store_generated", "store_id", "generated_at"),
    )


class ReportSnapshot(Base):
    """Monthly (or custom-period) report snapshot for MoM learning."""
    __tablename__ = "report_snapshots"

    snapshot_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    period_type = Column(Text, nullable=False, default="monthly")
    kpis_json = Column(JSON, nullable=True)
    top_actions_json = Column(JSON, nullable=True)
    model_versions_json = Column(JSON, nullable=True)
    mape_summary_json = Column(JSON, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "store_id", "period_start", "period_end", "period_type",
            name="uq_report_snapshot_period",
        ),
        Index("ix_report_snapshots_store", "store_id", "period_end"),
    )


# ---------------------------------------------------------------------------
# Revenue facts (Step 3) — stable rollups for D/W/M analytics
# ---------------------------------------------------------------------------

class DailyRevenueFact(Base):
    """Per-store daily revenue aggregate (source for week/month rollups)."""
    __tablename__ = "daily_revenue_facts"

    fact_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    fact_date = Column(Date, nullable=False)
    revenue = Column(Numeric(14, 2), nullable=False, default=0)
    transactions = Column(Integer, nullable=False, default=0)
    units_sold = Column(Numeric(14, 2), nullable=False, default=0)
    avg_basket_value = Column(Numeric(14, 2), nullable=False, default=0)
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("store_id", "fact_date", name="uq_daily_revenue_store_date"),
        Index("ix_daily_revenue_date", "fact_date"),
        Index("ix_daily_revenue_store", "store_id", "fact_date"),
    )


class MonthlyRevenueFact(Base):
    """
    Month-end revenue rollup. Once written, history stays stable even if
    raw invoices are later corrected — re-run rollup only to refresh.
    """
    __tablename__ = "monthly_revenue_facts"

    fact_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 1–12
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    revenue = Column(Numeric(14, 2), nullable=False, default=0)
    transactions = Column(Integer, nullable=False, default=0)
    units_sold = Column(Numeric(14, 2), nullable=False, default=0)
    avg_basket_value = Column(Numeric(14, 2), nullable=False, default=0)
    # [{category, revenue, units}, ...]
    by_category_json = Column(JSON, nullable=True)
    # [{product_id, product_name, revenue, units}, ...] top products
    by_product_json = Column(JSON, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow)
    # True once month is closed (period_end < today); still refreshable via CLI
    is_frozen = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("store_id", "year", "month", name="uq_monthly_revenue_store_ym"),
        Index("ix_monthly_revenue_period", "year", "month"),
        Index("ix_monthly_revenue_store", "store_id", "year", "month"),
    )


# ---------------------------------------------------------------------------
# Automation & monitoring (Step 9)
# ---------------------------------------------------------------------------

class JobRun(Base):
    """Log of scheduled / manual automation jobs (ML refresh, health checks, …)."""
    __tablename__ = "job_runs"

    job_id = Column(String, primary_key=True, default=gen_uuid)
    job_type = Column(Text, nullable=False)  # ml_refresh | health_check | rollup_revenue | …
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=True)  # null = platform-wide
    status = Column(Text, nullable=False, default="running")  # running | success | failed | skipped
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    detail_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    triggered_by = Column(Text, nullable=True)  # scheduler | cli | api | report

    __table_args__ = (
        Index("ix_job_runs_type_started", "job_type", "started_at"),
        Index("ix_job_runs_store_started", "store_id", "started_at"),
        Index("ix_job_runs_status", "status"),
    )


class MonitoringAlert(Base):
    """Open/closed alerts from MAPE drift, confidence collapse, ingestion health."""
    __tablename__ = "monitoring_alerts"

    alert_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, ForeignKey("stores.store_id"), nullable=True)
    alert_type = Column(Text, nullable=False)  # mape_drift | confidence_collapse | ingestion_errors
    severity = Column(Text, nullable=False, default="warning")  # info | warning | critical
    title = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    detail_json = Column(JSON, nullable=True)
    status = Column(Text, nullable=False, default="open")  # open | acknowledged | resolved
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_monitoring_alerts_status", "status", "created_at"),
        Index("ix_monitoring_alerts_store", "store_id", "status"),
        Index("ix_monitoring_alerts_type", "alert_type"),
    )
