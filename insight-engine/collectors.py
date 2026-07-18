"""
Collectors — read ML result tables (+ light loyalty aggregates) into plain dicts.
No scoring or templating here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional


@dataclass
class StoreSnapshot:
    """Frozen ML + loyalty signals for one store."""
    store_id: str
    association_rules: list[dict] = field(default_factory=list)
    price_elasticity: list[dict] = field(default_factory=list)
    inventory_signals: list[dict] = field(default_factory=list)
    customer_segments: list[dict] = field(default_factory=list)
    festival_insights: list[dict] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)
    abc_rows: list[dict] = field(default_factory=list)
    forecast_rows: list[dict] = field(default_factory=list)
    period_start: Optional[date] = None
    period_end: Optional[date] = None


def _f(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    return float(v)


def collect_store_snapshot(session, store_id: str) -> StoreSnapshot:
    from common.result_models import (
        AssociationRule,
        PriceElasticity,
        InventorySignal,
        CustomerSegment,
        FestivalInsight,
        Anomaly,
        ABCClassification,
        ForecastResult,
    )

    snap = StoreSnapshot(store_id=store_id)

    for r in (
        session.query(AssociationRule)
        .filter(
            (AssociationRule.store_id == store_id) | (AssociationRule.store_id.is_(None))
        )
        .all()
    ):
        snap.association_rules.append({
            "rule_id": r.rule_id,
            "store_id": r.store_id,
            "antecedent_product_ids": list(r.antecedent_product_ids or []),
            "consequent_product_ids": list(r.consequent_product_ids or []),
            "support": _f(r.support),
            "confidence": _f(r.confidence),
            "lift": _f(r.lift),
            "suggestion_text": r.suggestion_text,
        })

    for r in (
        session.query(PriceElasticity)
        .filter(
            (PriceElasticity.store_id == store_id) | (PriceElasticity.store_id.is_(None))
        )
        .all()
    ):
        snap.price_elasticity.append({
            "elasticity_id": r.elasticity_id,
            "product_id": r.product_id,
            "elasticity_coefficient": r.elasticity_coefficient,
            "r_squared": r.r_squared,
            "classification": r.classification,
            "promo_floor_flag": bool(r.promo_floor_flag),
            "margin_safe": r.margin_safe,
            "suggestion_text": r.suggestion_text,
            "period_start": r.period_start,
            "period_end": r.period_end,
        })

    for r in session.query(InventorySignal).filter_by(store_id=store_id).all():
        snap.inventory_signals.append({
            "signal_id": r.signal_id,
            "product_id": r.product_id,
            "signal_type": r.signal_type,
            "days_since_sale": r.days_since_sale,
            "recent_units_per_day": _f(r.recent_units_per_day) if r.recent_units_per_day is not None else None,
            "baseline_units_per_day": _f(r.baseline_units_per_day) if r.baseline_units_per_day is not None else None,
            "severity": r.severity,
            "suggestion_text": r.suggestion_text,
        })

    for r in session.query(CustomerSegment).filter_by(store_id=store_id).all():
        snap.customer_segments.append({
            "customer_id": r.customer_id,
            "phone_normalized": r.phone_normalized,
            "segment": r.segment,
            "churn_score": _f(r.churn_score),
            "monetary": _f(r.monetary),
            "recency_days": r.recency_days,
            "frequency": r.frequency,
        })

    for r in session.query(FestivalInsight).filter_by(store_id=store_id).all():
        snap.festival_insights.append({
            "festival_insight_id": r.festival_insight_id,
            "festival_name": r.festival_name,
            "festival_year": r.festival_year,
            "window_start": r.window_start,
            "window_end": r.window_end,
            "product_id": r.product_id,
            "lift_vs_baseline": _f(r.lift_vs_baseline) if r.lift_vs_baseline is not None else None,
            "action": r.action,
            "suggestion_text": r.suggestion_text,
            "evidence_json": r.evidence_json,
        })

    for r in session.query(Anomaly).filter_by(store_id=store_id).all():
        snap.anomalies.append({
            "anomaly_id": r.anomaly_id,
            "product_id": r.product_id,
            "anomaly_date": r.anomaly_date,
            "metric": r.metric,
            "actual_value": _f(r.actual_value),
            "expected_value": _f(r.expected_value),
            "z_score": _f(r.z_score),
            "severity": r.severity,
        })

    for r in (
        session.query(ABCClassification)
        .filter(
            (ABCClassification.store_id == store_id) | (ABCClassification.store_id.is_(None))
        )
        .all()
    ):
        snap.abc_rows.append({
            "product_id": r.product_id,
            "product_class": r.product_class,
            "revenue_rank": r.revenue_rank,
            "cumulative_pct": _f(r.cumulative_pct),
        })

    for r in session.query(ForecastResult).filter_by(store_id=store_id).all():
        snap.forecast_rows.append({
            "product_id": r.product_id,
            "forecast_date": r.forecast_date,
            "predicted_units": _f(r.predicted_units) if r.predicted_units is not None else None,
            "confidence_level": r.confidence_level,
            "backtest_mape_pct": r.backtest_mape_pct,
        })

    return snap
