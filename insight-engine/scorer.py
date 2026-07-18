"""Scoring + priority banding."""
from __future__ import annotations

from rules import CandidateAction


def score_action(action: CandidateAction) -> float:
    """score = impact × confidence × urgency × rule weight."""
    return round(
        float(action.impact_estimate)
        * float(action.confidence)
        * float(action.urgency)
        * float(action.weight),
        4,
    )


def priority_band(score: float, cfg: dict) -> str:
    scoring = cfg.get("scoring", {})
    if score >= float(scoring.get("priority_high_min", 5000)):
        return "high"
    if score >= float(scoring.get("priority_medium_min", 1000)):
        return "medium"
    return "low"


def attach_scores(actions: list[CandidateAction], cfg: dict) -> list[dict]:
    """Return scored dicts sorted by score descending (stable for ties)."""
    rows = []
    for i, a in enumerate(actions):
        s = score_action(a)
        rows.append({
            "action_type": a.action_type,
            "product_ids": list(a.product_ids),
            "impact_estimate": a.impact_estimate,
            "confidence": a.confidence,
            "urgency": a.urgency,
            "weight": a.weight,
            "score": s,
            "priority": priority_band(s, cfg),
            "evidence": a.evidence,
            "source_models": list(a.source_models),
            "title_vars": a.title_vars,
            "body_vars": a.body_vars,
            "suggestion_text": a.suggestion_text,
            "_order": i,  # stable tie-break by emission order
        })
    rows.sort(key=lambda r: (-r["score"], r["_order"]))
    for r in rows:
        del r["_order"]
    return rows
