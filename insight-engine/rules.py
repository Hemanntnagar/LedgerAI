"""
Rule library — one rule per action type. Emits CandidateAction dicts
before scoring / conflict resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from collectors import StoreSnapshot


@dataclass
class CandidateAction:
    action_type: str
    product_ids: list[str] = field(default_factory=list)
    impact_estimate: float = 0.0
    confidence: float = 0.5
    urgency: float = 0.5
    weight: float = 1.0
    evidence: dict = field(default_factory=dict)
    source_models: list[str] = field(default_factory=list)
    # Template keys
    title_vars: dict = field(default_factory=dict)
    body_vars: dict = field(default_factory=dict)
    # Optional pre-rendered text from ML suggestion_text
    suggestion_text: Optional[str] = None


def _conf_map(cfg: dict, level: str) -> float:
    return float(cfg.get("confidence_map", {}).get(level, 0.5))


def _urg_map(cfg: dict, level: str) -> float:
    return float(cfg.get("urgency_map", {}).get(level, 0.5))


def _pname(names: dict, pid: Optional[str]) -> str:
    if not pid:
        return "store"
    return names.get(pid, pid)


def apply_all_rules(
    snap: StoreSnapshot,
    cfg: dict,
    product_names: dict[str, str],
) -> list[CandidateAction]:
    out: list[CandidateAction] = []
    rules = cfg.get("rules", {})

    out.extend(_rule_bundle(snap, rules.get("bundle", {}), cfg, product_names))
    out.extend(_rule_stop_discount(snap, rules.get("stop_discount", {}), cfg, product_names))
    out.extend(_rule_reorder(snap, rules.get("reorder", {}), cfg, product_names))
    out.extend(_rule_clear_dead_stock(snap, rules.get("clear_dead_stock", {}), cfg, product_names))
    out.extend(_rule_investigate_velocity(snap, rules.get("investigate_velocity", {}), cfg, product_names))
    out.extend(_rule_win_back(snap, rules.get("win_back", {}), cfg, product_names))
    out.extend(_rule_vip_nurture(snap, rules.get("vip_nurture", {}), cfg, product_names))
    out.extend(_rule_festival_prep(snap, rules.get("festival_prep", {}), cfg, product_names))
    out.extend(_rule_anomaly_review(snap, rules.get("anomaly_review", {}), cfg, product_names))
    out.extend(_rule_protect_a_sku(snap, rules.get("protect_a_sku", {}), cfg, product_names))
    return out


def _take_top(cands: list[CandidateAction], n: int) -> list[CandidateAction]:
    if n <= 0:
        return []
    cands = sorted(
        cands,
        key=lambda c: (c.impact_estimate * c.confidence * c.urgency * c.weight),
        reverse=True,
    )
    return cands[:n]


def _rule_bundle(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    cands = []
    for r in snap.association_rules:
        if r["confidence"] < rule.get("min_confidence", 0.35):
            continue
        if r["lift"] < rule.get("min_lift", 1.2):
            continue
        if r["support"] < rule.get("min_support", 0.01):
            continue
        ants = r["antecedent_product_ids"]
        cons = r["consequent_product_ids"]
        a_names = ", ".join(_pname(names, p) for p in ants)
        c_names = ", ".join(_pname(names, p) for p in cons)
        impact = float(rule.get("assumed_basket_lift_rupees", 40)) * r["confidence"] * max(r["lift"], 1.0)
        cands.append(CandidateAction(
            action_type="bundle",
            product_ids=list(ants) + list(cons),
            impact_estimate=round(impact, 2),
            confidence=min(0.95, r["confidence"]),
            urgency=_urg_map(cfg, "medium"),
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "support": r["support"],
                "confidence": r["confidence"],
                "lift": r["lift"],
                "antecedents": ants,
                "consequents": cons,
            },
            source_models=["association_rules"],
            title_vars={"antecedents": a_names, "consequents": c_names},
            body_vars={
                "antecedents": a_names,
                "consequents": c_names,
                "pct": round(r["confidence"] * 100),
                "lift": round(r["lift"], 2),
                "impact": round(impact, 0),
            },
            suggestion_text=r.get("suggestion_text"),
        ))
    return _take_top(cands, int(rule.get("max_per_store", 8)))


def _rule_stop_discount(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    cands = []
    for r in snap.price_elasticity:
        if rule.get("require_promo_floor", True) and not r["promo_floor_flag"]:
            continue
        margin_unsafe = r["margin_safe"] is False
        elastic = (r["classification"] or "").lower() == "elastic"
        if rule.get("require_margin_unsafe_or_elastic", True) and not (margin_unsafe or elastic):
            continue
        pid = r["product_id"]
        impact = float(rule.get("assumed_monthly_waste_rupees", 800))
        if elastic and r["elasticity_coefficient"] is not None:
            # More elastic → higher waste estimate
            impact *= min(2.0, 1.0 + abs(float(r["elasticity_coefficient"])) / 2.0)
        conf = _conf_map(cfg, "medium")
        if r["r_squared"] is not None:
            conf = max(0.25, min(0.9, float(r["r_squared"])))
        cands.append(CandidateAction(
            action_type="stop_discount",
            product_ids=[pid],
            impact_estimate=round(impact, 2),
            confidence=conf,
            urgency=_urg_map(cfg, "medium" if margin_unsafe else "low"),
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "classification": r["classification"],
                "promo_floor_flag": r["promo_floor_flag"],
                "margin_safe": r["margin_safe"],
                "elasticity_coefficient": r["elasticity_coefficient"],
                "r_squared": r["r_squared"],
            },
            source_models=["price_elasticity"],
            title_vars={"product": _pname(names, pid)},
            body_vars={
                "product": _pname(names, pid),
                "classification": r["classification"],
                "impact": round(impact, 0),
            },
            suggestion_text=r.get("suggestion_text"),
        ))
    return _take_top(cands, int(rule.get("max_per_store", 6)))


def _rule_reorder(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    types = set(rule.get("signal_types", ["velocity_surge"]))
    cands = []
    for r in snap.inventory_signals:
        if r["signal_type"] not in types:
            continue
        pid = r["product_id"]
        recent = r["recent_units_per_day"] or 0.0
        days = int(rule.get("days_horizon", 7))
        unit_rev = float(rule.get("assumed_unit_revenue", 50))
        impact = recent * days * unit_rev
        # Boost from forecast if available
        conf = _conf_map(cfg, "medium")
        urg = _urg_map(cfg, r.get("severity") or "medium")
        for f in snap.forecast_rows:
            if f["product_id"] == pid and f.get("predicted_units"):
                conf = _conf_map(cfg, f.get("confidence_level") or "medium")
                break
        cands.append(CandidateAction(
            action_type="reorder",
            product_ids=[pid],
            impact_estimate=round(impact, 2),
            confidence=conf,
            urgency=urg,
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "signal_type": r["signal_type"],
                "recent_units_per_day": recent,
                "baseline_units_per_day": r["baseline_units_per_day"],
                "severity": r["severity"],
            },
            source_models=["inventory_signals", "forecast_results"],
            title_vars={"product": _pname(names, pid)},
            body_vars={
                "product": _pname(names, pid),
                "recent": round(recent, 1),
                "days": days,
                "impact": round(impact, 0),
            },
            suggestion_text=r.get("suggestion_text"),
        ))
    return _take_top(cands, int(rule.get("max_per_store", 6)))


def _rule_clear_dead_stock(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    types = set(rule.get("signal_types", ["dead_stock"]))
    cands = []
    for r in snap.inventory_signals:
        if r["signal_type"] not in types:
            continue
        pid = r["product_id"]
        days = r["days_since_sale"] or 0
        impact = float(rule.get("assumed_recovery_rupees", 500)) * (1 + min(days, 90) / 90.0)
        cands.append(CandidateAction(
            action_type="clear_dead_stock",
            product_ids=[pid],
            impact_estimate=round(impact, 2),
            confidence=_conf_map(cfg, "high"),
            urgency=_urg_map(cfg, r.get("severity") or "medium"),
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "days_since_sale": days,
                "severity": r["severity"],
            },
            source_models=["inventory_signals"],
            title_vars={"product": _pname(names, pid)},
            body_vars={
                "product": _pname(names, pid),
                "days": days,
                "impact": round(impact, 0),
            },
            suggestion_text=r.get("suggestion_text"),
        ))
    return _take_top(cands, int(rule.get("max_per_store", 5)))


def _rule_investigate_velocity(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    types = set(rule.get("signal_types", ["velocity_drop"]))
    cands = []
    for r in snap.inventory_signals:
        if r["signal_type"] not in types:
            continue
        pid = r["product_id"]
        impact = float(rule.get("assumed_monthly_loss_rupees", 600))
        cands.append(CandidateAction(
            action_type="investigate_velocity",
            product_ids=[pid],
            impact_estimate=round(impact, 2),
            confidence=_conf_map(cfg, "medium"),
            urgency=_urg_map(cfg, r.get("severity") or "medium"),
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "recent_units_per_day": r["recent_units_per_day"],
                "baseline_units_per_day": r["baseline_units_per_day"],
            },
            source_models=["inventory_signals"],
            title_vars={"product": _pname(names, pid)},
            body_vars={
                "product": _pname(names, pid),
                "recent": round(r["recent_units_per_day"] or 0, 1),
                "baseline": round(r["baseline_units_per_day"] or 0, 1),
            },
            suggestion_text=r.get("suggestion_text"),
        ))
    return _take_top(cands, int(rule.get("max_per_store", 5)))


def _rule_win_back(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    segs = set(rule.get("segments", ["dormant", "at_risk"]))
    targets = [c for c in snap.customer_segments if c["segment"] in segs]
    if len(targets) < int(rule.get("min_customers", 3)):
        return []
    avg_mon = sum(c["monetary"] for c in targets) / len(targets) if targets else 0
    rate = float(rule.get("assumed_reactivation_rate", 0.08))
    impact = len(targets) * avg_mon * rate
    phones = [c["phone_normalized"] for c in targets if c.get("phone_normalized")][:50]
    return [CandidateAction(
        action_type="win_back",
        product_ids=[],
        impact_estimate=round(impact, 2),
        confidence=_conf_map(cfg, "medium"),
        urgency=_urg_map(cfg, "medium"),
        weight=float(rule.get("weight", 1.0)),
        evidence={
            "customer_count": len(targets),
            "segment_breakdown": {
                s: sum(1 for c in targets if c["segment"] == s) for s in segs
            },
            "sample_phones": phones[:10],
            "avg_monetary": round(avg_mon, 2),
        },
        source_models=["customer_segments"],
        title_vars={"count": len(targets)},
        body_vars={
            "count": len(targets),
            "impact": round(impact, 0),
            "rate_pct": round(rate * 100),
        },
    )]


def _rule_vip_nurture(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    segs = set(rule.get("segments", ["vip"]))
    vips = [c for c in snap.customer_segments if c["segment"] in segs]
    if len(vips) < int(rule.get("min_customers", 2)):
        return []
    per = float(rule.get("assumed_retention_lift_rupees_per_vip", 200))
    impact = len(vips) * per
    return [CandidateAction(
        action_type="vip_nurture",
        product_ids=[],
        impact_estimate=round(impact, 2),
        confidence=_conf_map(cfg, "high"),
        urgency=_urg_map(cfg, "low"),
        weight=float(rule.get("weight", 1.0)),
        evidence={
            "vip_count": len(vips),
            "sample_phones": [c["phone_normalized"] for c in vips if c.get("phone_normalized")][:10],
        },
        source_models=["customer_segments"],
        title_vars={"count": len(vips)},
        body_vars={"count": len(vips), "impact": round(impact, 0)},
    )]


def _rule_festival_prep(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    allowed = set(rule.get("actions", ["stock_up", "offer"]))
    min_lift = float(rule.get("min_lift", 1.15))
    window_rev = float(rule.get("assumed_window_revenue", 3000))
    cands = []
    for r in snap.festival_insights:
        if r["action"] not in allowed:
            continue
        lift = r["lift_vs_baseline"]
        if lift is not None and lift < min_lift:
            continue
        pid = r["product_id"]
        lift_f = lift if lift is not None else 1.2
        impact = (lift_f - 1.0) * window_rev
        urg = _urg_map(cfg, "high")  # festivals are time-bound
        cands.append(CandidateAction(
            action_type="festival_prep",
            product_ids=[pid] if pid else [],
            impact_estimate=round(impact, 2),
            confidence=_conf_map(cfg, "medium"),
            urgency=urg,
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "festival_name": r["festival_name"],
                "festival_year": r["festival_year"],
                "window_start": str(r["window_start"]),
                "window_end": str(r["window_end"]),
                "lift_vs_baseline": lift,
                "action": r["action"],
            },
            source_models=["festival_insights"],
            title_vars={
                "festival": r["festival_name"],
                "product": _pname(names, pid) if pid else "top SKUs",
                "action": r["action"].replace("_", " "),
            },
            body_vars={
                "festival": r["festival_name"],
                "product": _pname(names, pid) if pid else "top SKUs",
                "lift_pct": round((lift_f - 1) * 100) if lift is not None else None,
                "window_start": str(r["window_start"]),
                "window_end": str(r["window_end"]),
                "impact": round(impact, 0),
            },
            suggestion_text=r.get("suggestion_text"),
        ))
    return _take_top(cands, int(rule.get("max_per_store", 8)))


def _rule_anomaly_review(snap, rule, cfg, names) -> list[CandidateAction]:
    if not rule.get("enabled", True):
        return []
    sevs = set(rule.get("severities", ["high", "medium"]))
    cands = []
    for r in snap.anomalies:
        if r["severity"] not in sevs:
            continue
        impact = abs(r["actual_value"] - r["expected_value"])
        pid = r["product_id"]
        cands.append(CandidateAction(
            action_type="anomaly_review",
            product_ids=[pid] if pid else [],
            impact_estimate=round(impact, 2),
            confidence=_conf_map(cfg, "medium"),
            urgency=_urg_map(cfg, r["severity"]),
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "anomaly_date": str(r["anomaly_date"]),
                "metric": r["metric"],
                "actual_value": r["actual_value"],
                "expected_value": r["expected_value"],
                "z_score": r["z_score"],
                "severity": r["severity"],
            },
            source_models=["anomalies"],
            title_vars={
                "product": _pname(names, pid) if pid else "store",
                "metric": r["metric"],
            },
            body_vars={
                "product": _pname(names, pid) if pid else "store",
                "metric": r["metric"],
                "date": str(r["anomaly_date"]),
                "actual": round(r["actual_value"], 0),
                "expected": round(r["expected_value"], 0),
                "z": round(r["z_score"], 1),
            },
        ))
    return _take_top(cands, int(rule.get("max_per_store", 4)))


def _rule_protect_a_sku(snap, rule, cfg, names) -> list[CandidateAction]:
    """Flag A-class SKUs with no inventory signal — keep attention on revenue drivers."""
    if not rule.get("enabled", True):
        return []
    signaled = {s["product_id"] for s in snap.inventory_signals}
    target_class = rule.get("product_class", "A")
    cands = []
    for r in snap.abc_rows:
        if r["product_class"] != target_class:
            continue
        pid = r["product_id"]
        if pid in signaled:
            continue
        impact = float(rule.get("assumed_monthly_risk_rupees", 1500))
        cands.append(CandidateAction(
            action_type="protect_a_sku",
            product_ids=[pid],
            impact_estimate=round(impact, 2),
            confidence=_conf_map(cfg, "low"),
            urgency=_urg_map(cfg, "low"),
            weight=float(rule.get("weight", 1.0)),
            evidence={
                "product_class": r["product_class"],
                "revenue_rank": r["revenue_rank"],
                "cumulative_pct": r["cumulative_pct"],
            },
            source_models=["abc_classification"],
            title_vars={"product": _pname(names, pid)},
            body_vars={
                "product": _pname(names, pid),
                "rank": r["revenue_rank"],
            },
        ))
    return _take_top(cands, int(rule.get("max_per_store", 3)))
