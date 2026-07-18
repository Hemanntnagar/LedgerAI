"""
Step 8 — Read ML result tables for store-app Intelligence views.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from dbconn import Product
from ml_path import load_result_models


def _product_names(session: Session) -> dict[str, str]:
    return {p.product_id: p.product_name for p in session.query(Product).all()}


def _name(names: dict[str, str], product_id: Optional[str]) -> Optional[str]:
    if not product_id:
        return None
    return names.get(product_id, product_id)


def _names_list(names: dict[str, str], ids) -> list[str]:
    if not ids:
        return []
    return [_name(names, i) or str(i) for i in ids]


def ml_module_counts(session: Session, store_id: str) -> dict[str, int]:
    rm = load_result_models()
    return {
        "forecast": session.query(rm.ForecastResult)
        .filter_by(store_id=store_id)
        .count(),
        "bundles": session.query(rm.AssociationRule)
        .filter(
            (rm.AssociationRule.store_id == store_id)
            | (rm.AssociationRule.store_id.is_(None))
        )
        .count(),
        "pricing": session.query(rm.PriceElasticity)
        .filter(
            (rm.PriceElasticity.store_id == store_id)
            | (rm.PriceElasticity.store_id.is_(None))
        )
        .count(),
        "inventory": session.query(rm.InventorySignal)
        .filter_by(store_id=store_id)
        .count(),
        "customers": session.query(rm.CustomerSegment)
        .filter_by(store_id=store_id)
        .count(),
        "festivals": session.query(rm.FestivalInsight)
        .filter_by(store_id=store_id)
        .count(),
    }


def get_forecast_rows(session: Session, store_id: str, limit: int = 50) -> list[dict]:
    rm = load_result_models()
    names = _product_names(session)
    rows = (
        session.query(rm.ForecastResult)
        .filter_by(store_id=store_id)
        .order_by(rm.ForecastResult.forecast_date.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "forecast_id": r.forecast_id,
            "product_id": r.product_id,
            "product_name": _name(names, r.product_id),
            "forecast_date": r.forecast_date.isoformat() if r.forecast_date else None,
            "predicted_units": float(r.predicted_units) if r.predicted_units is not None else None,
            "predicted_revenue": float(r.predicted_revenue)
            if r.predicted_revenue is not None
            else None,
            "confidence_level": r.confidence_level,
            "backtest_mape_pct": r.backtest_mape_pct,
            "grain": r.grain,
            "model_version": r.model_version,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        }
        for r in rows
    ]


def get_bundle_rows(session: Session, store_id: str, limit: int = 50) -> list[dict]:
    rm = load_result_models()
    names = _product_names(session)
    rows = (
        session.query(rm.AssociationRule)
        .filter(
            (rm.AssociationRule.store_id == store_id)
            | (rm.AssociationRule.store_id.is_(None))
        )
        .order_by(rm.AssociationRule.lift.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "rule_id": r.rule_id,
            "antecedents": _names_list(names, r.antecedent_product_ids),
            "consequents": _names_list(names, r.consequent_product_ids),
            "support": r.support,
            "confidence": r.confidence,
            "lift": r.lift,
            "suggestion_text": r.suggestion_text,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        }
        for r in rows
    ]


def get_pricing_rows(session: Session, store_id: str, limit: int = 50) -> list[dict]:
    rm = load_result_models()
    names = _product_names(session)
    rows = (
        session.query(rm.PriceElasticity)
        .filter(
            (rm.PriceElasticity.store_id == store_id)
            | (rm.PriceElasticity.store_id.is_(None))
        )
        .order_by(rm.PriceElasticity.generated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "elasticity_id": r.elasticity_id,
            "product_id": r.product_id,
            "product_name": _name(names, r.product_id),
            "elasticity_coefficient": r.elasticity_coefficient,
            "r_squared": r.r_squared,
            "classification": r.classification,
            "promo_floor_flag": r.promo_floor_flag,
            "margin_safe": r.margin_safe,
            "suggestion_text": r.suggestion_text,
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
        }
        for r in rows
    ]


def get_inventory_rows(session: Session, store_id: str, limit: int = 50) -> list[dict]:
    rm = load_result_models()
    names = _product_names(session)
    rows = (
        session.query(rm.InventorySignal)
        .filter_by(store_id=store_id)
        .order_by(rm.InventorySignal.generated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "signal_id": r.signal_id,
            "product_id": r.product_id,
            "product_name": _name(names, r.product_id),
            "signal_type": r.signal_type,
            "days_since_sale": r.days_since_sale,
            "recent_units_per_day": r.recent_units_per_day,
            "baseline_units_per_day": r.baseline_units_per_day,
            "severity": r.severity,
            "suggestion_text": r.suggestion_text,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        }
        for r in rows
    ]


def get_customer_rows(session: Session, store_id: str, limit: int = 50) -> list[dict]:
    rm = load_result_models()
    rows = (
        session.query(rm.CustomerSegment)
        .filter_by(store_id=store_id)
        .order_by(rm.CustomerSegment.monetary.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "segment_id": r.segment_id,
            "customer_id": r.customer_id,
            "phone_normalized": r.phone_normalized,
            "recency_days": r.recency_days,
            "frequency": r.frequency,
            "monetary": float(r.monetary) if r.monetary is not None else None,
            "segment": r.segment,
            "churn_score": r.churn_score,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        }
        for r in rows
    ]


def get_festival_rows(session: Session, store_id: str, limit: int = 50) -> list[dict]:
    rm = load_result_models()
    names = _product_names(session)
    rows = (
        session.query(rm.FestivalInsight)
        .filter_by(store_id=store_id)
        .order_by(rm.FestivalInsight.festival_year.desc(), rm.FestivalInsight.festival_name)
        .limit(limit)
        .all()
    )
    return [
        {
            "festival_insight_id": r.festival_insight_id,
            "festival_name": r.festival_name,
            "festival_year": r.festival_year,
            "window_start": r.window_start.isoformat() if r.window_start else None,
            "window_end": r.window_end.isoformat() if r.window_end else None,
            "product_id": r.product_id,
            "product_name": _name(names, r.product_id),
            "lift_vs_baseline": r.lift_vs_baseline,
            "yoy_lift": r.yoy_lift,
            "action": r.action,
            "suggestion_text": r.suggestion_text,
            "generated_at": r.generated_at.isoformat() + "Z" if r.generated_at else None,
        }
        for r in rows
    ]
