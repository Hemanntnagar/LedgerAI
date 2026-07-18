"""
Step 6 — Deterministic Insight Engine.

Same DB snapshot + rules.yaml → same ranked action list.
Zero network calls to LLM APIs.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from collectors import collect_store_snapshot
from config_loader import load_rules_config
from dedupe import resolve_conflicts
from learning import apply_learned_weights, learned_weight_multipliers
from persist import persist_insights
from rules import apply_all_rules
from scorer import attach_scores

logger = logging.getLogger("insight_engine")


def run_insight_engine(
    store_id: Optional[str] = None,
    store_name: Optional[str] = None,
    *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    config_path: Optional[str] = None,
    persist: bool = True,
    session=None,
) -> dict[str, Any]:
    """
    Collect ML signals → apply rules → score → resolve conflicts → persist.

    Returns summary with ranked action dicts (title/body included).
    """
    from dbconn import (
        ensure_ml_tables,
        get_session,
        init_db,
        load_product_names,
        resolve_store_id,
        Base,
        engine,
    )
    from templates import render_action

    ensure_ml_tables()
    Base.metadata.create_all(bind=engine)

    cfg = load_rules_config(config_path)
    own_session = session is None
    session = session or get_session()

    try:
        if own_session:
            init_db()

        sid = resolve_store_id(session, store_id=store_id, store_name=store_name)
        if not sid:
            raise ValueError("store_id or store_name is required")

        snap = collect_store_snapshot(session, sid)
        snap.period_start = period_start
        snap.period_end = period_end
        names = load_product_names(session)

        candidates = apply_all_rules(snap, cfg, names)
        # Step 7: feedback-tuned multipliers (still deterministic given DB state)
        multipliers = learned_weight_multipliers(session, sid, cfg)
        apply_learned_weights(candidates, multipliers)
        scored = attach_scores(candidates, cfg)
        resolved = resolve_conflicts(scored, cfg)

        max_n = int(cfg.get("scoring", {}).get("max_insights_per_store", 40))
        resolved = resolved[:max_n]

        # Attach rendered text for API consumers
        actions = []
        for row in resolved:
            title, body = render_action(row)
            actions.append({
                "action_type": row["action_type"],
                "priority": row["priority"],
                "title": title,
                "body": body,
                "impact_estimate": row["impact_estimate"],
                "confidence": row["confidence"],
                "urgency": row["urgency"],
                "score": row["score"],
                "product_ids": row["product_ids"],
                "evidence": row["evidence"],
                "source_models": row["source_models"],
            })

        created = []
        if persist:
            replace = cfg.get("scoring", {}).get("replace_status", "new")
            created = persist_insights(
                session,
                sid,
                resolved,
                period_start=period_start,
                period_end=period_end,
                replace_status=replace,
            )
            if own_session:
                session.commit()

        summary = {
            "store_id": sid,
            "candidates": len(candidates),
            "after_conflicts": len(resolved),
            "persisted": len(created),
            "learned_weight_multipliers": multipliers,
            "actions": actions,
        }
        logger.info(
            "Insight engine store=%s candidates=%s kept=%s",
            sid, len(candidates), len(resolved),
        )
        return summary
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()
