"""
Result tables for all ML modules. Uses the SAME Base/engine as the
Phase 1 pipeline models, so these tables live in the same database
and get created via the same Base.metadata.create_all() call.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Numeric, Integer, Date, DateTime, Text, JSON, Float
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
    confidence_level = Column(Text, nullable=False)  # 'high' | 'low'
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
