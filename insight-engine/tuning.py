"""
Step 10 — Threshold tuning helpers.

Simulate rule parameter overrides against the current ML snapshot
(no persist). Compare candidate counts / score distribution; optionally
overlap with historical useful/not_useful feedback.
"""
from __future__ import annotations

import copy
from typing import Any, Optional


def _deep_set(cfg: dict, path: list[str], value: Any) -> None:
    cur = cfg
    for key in path[:-1]:
        cur = cur.setdefault(key, {})
    cur[path[-1]] = value


def _resolve_store(session, store_id: Optional[str], store_name: Optional[str]) -> str:
    from db.models import Store

    if store_id:
        return store_id
    if not store_name:
        raise ValueError("store_id or store_name is required")
    store = session.query(Store).filter(Store.store_name == store_name).first()
    if not store:
        raise ValueError(f"Store not found: {store_name}")
    return store.store_id


def simulate_rule_thresholds(
    store_id: Optional[str] = None,
    store_name: Optional[str] = None,
    *,
    rule_name: str,
    param: str,
    values: list[Any],
    session=None,
    config_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    For each candidate value of rules.<rule_name>.<param>, re-apply rules
    and report how many candidates fire (after scoring, before persist).
    """
    from collectors import collect_store_snapshot
    from config_loader import load_rules_config
    from dedupe import resolve_conflicts
    from learning import apply_learned_weights, learned_weight_multipliers
    from rules import apply_all_rules
    from scorer import attach_scores
    from templates import render_action
    from db.session import get_session, init_db, engine
    from db.models import Base, Insight
    from common import result_models  # noqa: F401 — register ML tables

    Base.metadata.create_all(bind=engine)

    own = session is None
    session = session or get_session()
    try:
        if own:
            init_db()
        sid = _resolve_store(session, store_id, store_name)

        base_cfg = load_rules_config(config_path)
        snap = collect_store_snapshot(session, sid)
        from db.models import Product

        names = {p.product_id: p.product_name for p in session.query(Product).all()}
        multipliers = learned_weight_multipliers(session, sid, base_cfg)

        feedback_rows = (
            session.query(Insight)
            .filter(
                Insight.store_id == sid,
                Insight.action_type == rule_name,
                Insight.feedback.isnot(None),
            )
            .all()
        )
        useful_titles = {
            (r.title or "").strip().lower()
            for r in feedback_rows
            if (r.feedback or "").strip().lower() == "useful"
        }
        not_useful_titles = {
            (r.title or "").strip().lower()
            for r in feedback_rows
            if (r.feedback or "").strip().lower() == "not_useful"
        }

        grid: list[dict[str, Any]] = []
        for val in values:
            cfg = copy.deepcopy(base_cfg)
            if rule_name not in cfg.get("rules", {}):
                raise ValueError(f"Unknown rule: {rule_name}")
            _deep_set(cfg, ["rules", rule_name, param], _coerce(val))

            candidates = apply_all_rules(snap, cfg, names)
            apply_learned_weights(candidates, multipliers)
            scored = attach_scores(candidates, cfg)
            resolved = resolve_conflicts(scored, cfg)
            of_rule = [c for c in resolved if c.get("action_type") == rule_name]
            titles = set()
            for c in of_rule:
                title, _body = render_action(c)
                titles.add((title or "").strip().lower())

            scores = [float(c.get("score") or 0) for c in of_rule]
            grid.append(
                {
                    "param": param,
                    "value": _coerce(val),
                    "n_candidates": len(of_rule),
                    "n_total_after_conflicts": len(resolved),
                    "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
                    "max_score": round(max(scores), 2) if scores else None,
                    "overlap_useful": len(titles & useful_titles),
                    "overlap_not_useful": len(titles & not_useful_titles),
                }
            )

        return {
            "store_id": sid,
            "rule": rule_name,
            "param": param,
            "grid": grid,
            "feedback_rows_for_rule": len(feedback_rows),
            "hint": (
                "Prefer values that keep overlap_useful high and overlap_not_useful low; "
                "then copy into insight-engine/config/rules.yaml. "
                "Festival season suggestions live in invoice-pipeline/config/pilot.yaml."
            ),
        }
    finally:
        if own:
            session.close()


def _coerce(val: Any) -> Any:
    if isinstance(val, str):
        s = val.strip()
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s
    return val


def compare_configs(
    store_id: str,
    *,
    config_a: Optional[str] = None,
    config_b: Optional[str] = None,
    session=None,
) -> dict[str, Any]:
    """Side-by-side dry-run of two rules.yaml paths."""
    from engine import run_insight_engine

    a = run_insight_engine(
        store_id=store_id, persist=False, config_path=config_a, session=session
    )
    b = run_insight_engine(
        store_id=store_id, persist=False, config_path=config_b, session=session
    )
    titles_a = {x["title"] for x in a.get("actions", [])}
    titles_b = {x["title"] for x in b.get("actions", [])}
    return {
        "store_id": store_id,
        "config_a": {
            "path": config_a or "default",
            "n_actions": len(a.get("actions", [])),
            "titles": sorted(titles_a),
        },
        "config_b": {
            "path": config_b or "default",
            "n_actions": len(b.get("actions", [])),
            "titles": sorted(titles_b),
        },
        "only_in_a": sorted(titles_a - titles_b),
        "only_in_b": sorted(titles_b - titles_a),
        "in_both": sorted(titles_a & titles_b),
    }
