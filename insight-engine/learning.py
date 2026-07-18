"""
Step 7 — Feedback → rule weight multipliers.

Simple Beta-style prior (no LLM): more "useful" marks raise the
action_type weight; more "not_useful" marks lower it. Clamped.
"""
from __future__ import annotations

from typing import Any


def feedback_counts_by_action_type(session, store_id: str) -> dict[str, dict[str, int]]:
    """Tally useful / not_useful feedback per action_type for one store."""
    from db.models import Insight

    rows = (
        session.query(Insight)
        .filter(
            Insight.store_id == store_id,
            Insight.feedback.isnot(None),
        )
        .all()
    )
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        fb = (r.feedback or "").strip().lower()
        if fb not in ("useful", "not_useful"):
            continue
        at = r.action_type or "unknown"
        bucket = out.setdefault(at, {"useful": 0, "not_useful": 0})
        bucket[fb] += 1
    return out


def weight_multiplier(
    useful: int,
    not_useful: int,
    *,
    prior_alpha: float = 2.0,
    prior_beta: float = 2.0,
    min_mult: float = 0.5,
    max_mult: float = 1.5,
) -> float:
    """
    Posterior mean of Beta(α+useful, β+not_useful), mapped so 0.5 → 1.0.

    weight = clamp(0.5 + posterior, min_mult, max_mult)
    """
    alpha = prior_alpha + max(0, useful)
    beta = prior_beta + max(0, not_useful)
    posterior = alpha / (alpha + beta)
    return round(max(min_mult, min(max_mult, 0.5 + posterior)), 4)


def learned_weight_multipliers(
    session,
    store_id: str,
    cfg: dict[str, Any],
) -> dict[str, float]:
    """
    Return {action_type: multiplier} for types that have feedback.
    Types with no feedback are omitted (caller keeps YAML base weight).
    """
    learning = cfg.get("learning", {})
    if learning.get("enabled", True) is False:
        return {}

    prior_alpha = float(learning.get("prior_alpha", 2.0))
    prior_beta = float(learning.get("prior_beta", 2.0))
    min_mult = float(learning.get("min_multiplier", 0.5))
    max_mult = float(learning.get("max_multiplier", 1.5))

    counts = feedback_counts_by_action_type(session, store_id)
    return {
        at: weight_multiplier(
            c.get("useful", 0),
            c.get("not_useful", 0),
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
            min_mult=min_mult,
            max_mult=max_mult,
        )
        for at, c in counts.items()
    }


def apply_learned_weights(
    candidates: list,
    multipliers: dict[str, float],
) -> list:
    """Multiply CandidateAction.weight by learned multiplier (in place)."""
    if not multipliers:
        return candidates
    for c in candidates:
        m = multipliers.get(c.action_type)
        if m is not None:
            c.weight = float(c.weight) * float(m)
            c.evidence = dict(c.evidence or {})
            c.evidence["learned_weight_multiplier"] = m
    return candidates
