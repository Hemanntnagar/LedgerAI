"""
Result tables for all ML modules. Uses the SAME Base/engine as the
Phase 1 pipeline models, so these tables live in the same database
and get created via the same Base.metadata.create_all() call.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, Text, JSON, Float, Boolean
from common.data_loaders import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class ForecastResult(Base):
    __tablename__ = "forecast_results"

    forecast_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    product_id = Column(String, nullable=False)
    forecast_date = Column(Date, nullable=False)
    predicted_units = Column(Numeric(12, 2), nullable=True)
    predicted_revenue = Column(Numeric(12, 2), nullable=True)
    lower_bound = Column(Numeric(12, 2), nullable=True)
    upper_bound = Column(Numeric(12, 2), nullable=True)
    confidence_level = Column(Text, nullable=False)  # 'high' | 'medium' | 'low'
    backtest_mape_pct = Column(Float, nullable=True)  # Step 5: MAPE that drove confidence
    grain = Column(Text, nullable=False, default="daily")  # daily | weekly
    model_version = Column(Text, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)


class AssociationRule(Base):
    __tablename__ = "association_rules"

    rule_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=True)  # null = all-store global rule
    antecedent_product_ids = Column(JSON, nullable=False)
    consequent_product_ids = Column(JSON, nullable=False)
    support = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    lift = Column(Float, nullable=False)
    suggestion_text = Column(Text, nullable=True)  # Step 5 template narrative
    generated_at = Column(DateTime, default=datetime.utcnow)


class ABCClassification(Base):
    __tablename__ = "abc_classification"

    classification_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=True)
    product_id = Column(String, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    revenue_rank = Column(Integer, nullable=False)
    cumulative_pct = Column(Float, nullable=False)
    product_class = Column(Text, nullable=False)  # 'A' | 'B' | 'C'
    generated_at = Column(DateTime, default=datetime.utcnow)


class PriceElasticity(Base):
    __tablename__ = "price_elasticity"

    elasticity_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=True)
    product_id = Column(String, nullable=False)
    elasticity_coefficient = Column(Float, nullable=True)
    r_squared = Column(Float, nullable=True)
    classification = Column(Text, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    # Step 5 pricing enrichment
    promo_floor_flag = Column(Boolean, nullable=False, default=False)
    margin_safe = Column(Boolean, nullable=True)
    assumed_margin_pct = Column(Float, nullable=True)
    suggestion_text = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)


class Anomaly(Base):
    __tablename__ = "anomalies"

    anomaly_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    product_id = Column(String, nullable=True)  # null = store-level anomaly
    anomaly_date = Column(Date, nullable=False)
    metric = Column(Text, nullable=False)  # 'revenue' | 'units'
    actual_value = Column(Numeric(12, 2), nullable=False)
    expected_value = Column(Numeric(12, 2), nullable=False)
    z_score = Column(Float, nullable=False)
    severity = Column(Text, nullable=False)  # 'high' | 'medium' | 'low'
    generated_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Step 5 — new result tables
# ---------------------------------------------------------------------------

class CustomerSegment(Base):
    """RFM / VIP / dormant / churn scores per phone-linked customer."""
    __tablename__ = "customer_segments"

    segment_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    customer_id = Column(String, nullable=False)
    phone_normalized = Column(String, nullable=True)
    recency_days = Column(Integer, nullable=False)
    frequency = Column(Integer, nullable=False)
    monetary = Column(Numeric(14, 2), nullable=False)
    r_score = Column(Integer, nullable=False)  # 1–5
    f_score = Column(Integer, nullable=False)
    m_score = Column(Integer, nullable=False)
    segment = Column(Text, nullable=False)  # vip | loyal | regular | dormant | at_risk
    churn_score = Column(Float, nullable=False)  # 0–1
    generated_at = Column(DateTime, default=datetime.utcnow)


class NextBestOffer(Base):
    """Checkout / campaign product suggestion for a customer."""
    __tablename__ = "next_best_offers"

    offer_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    customer_id = Column(String, nullable=False)
    product_id = Column(String, nullable=False)
    score = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    suggestion_text = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)


class InventorySignal(Base):
    """Dead stock / velocity alerts (sales proxies until stock feed exists)."""
    __tablename__ = "inventory_signals"

    signal_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    product_id = Column(String, nullable=False)
    signal_type = Column(Text, nullable=False)  # dead_stock | velocity_drop | velocity_surge
    days_since_sale = Column(Integer, nullable=True)
    recent_units_per_day = Column(Float, nullable=True)
    baseline_units_per_day = Column(Float, nullable=True)
    severity = Column(Text, nullable=False)  # high | medium | low
    suggestion_text = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)


class TimingInsight(Base):
    """Promo-day lift vs baseline + peak staffing hints."""
    __tablename__ = "timing_insights"

    insight_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    insight_type = Column(Text, nullable=False)  # promo_lift | peak_staffing
    label = Column(Text, nullable=False)  # festival name, weekday, or hour
    metric_value = Column(Float, nullable=False)  # lift ratio or revenue share
    baseline_value = Column(Float, nullable=True)
    suggestion_text = Column(Text, nullable=True)
    evidence_json = Column(JSON, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)


class StoreBenchmark(Base):
    """Peer-normalized underperformer / best-practice flags."""
    __tablename__ = "store_benchmarks"

    benchmark_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    metric = Column(Text, nullable=False)  # revenue | avg_basket | txn_count
    store_value = Column(Float, nullable=False)
    peer_median = Column(Float, nullable=False)
    pct_vs_peer = Column(Float, nullable=False)  # (store - median) / median * 100
    flag = Column(Text, nullable=False)  # underperformer | best_practice | in_line
    suggestion_text = Column(Text, nullable=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)


class FestivalInsight(Base):
    """Indian festival prep: YoY/pre-festival lift + stock/offer proposals."""
    __tablename__ = "festival_insights"

    festival_insight_id = Column(String, primary_key=True, default=gen_uuid)
    store_id = Column(String, nullable=False)
    festival_id = Column(String, nullable=True)
    festival_name = Column(Text, nullable=False)
    festival_year = Column(Integer, nullable=False)
    window_start = Column(Date, nullable=False)
    window_end = Column(Date, nullable=False)
    product_id = Column(String, nullable=True)  # null = store-level summary
    lift_vs_baseline = Column(Float, nullable=True)
    yoy_lift = Column(Float, nullable=True)
    action = Column(Text, nullable=False)  # stock_up | offer | watch
    suggestion_text = Column(Text, nullable=False)
    evidence_json = Column(JSON, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
