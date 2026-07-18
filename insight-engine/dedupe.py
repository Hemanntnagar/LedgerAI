"""
Conflict resolver — drop lower-scoring actions that contradict each other
on the same product (e.g. reorder vs clear_dead_stock).
"""
from __future__ import annotations


def _product_key(product_ids: list[str]) -> frozenset:
    return frozenset(product_ids or [])


def resolve_conflicts(scored: list[dict], cfg: dict) -> list[dict]:
    """
    Keep highest-scoring action when a configured mutex pair shares ≥1 product.
    Input must already be sorted by score desc.
    """
    pairs = cfg.get("conflicts", {}).get("product_mutex_pairs", [])
    mutex: set[frozenset] = {frozenset(p) for p in pairs if len(p) == 2}

    kept: list[dict] = []
    # For each product, track which action_types we already kept
    product_types: dict[str, set[str]] = {}

    for row in scored:
        atype = row["action_type"]
        pids = row.get("product_ids") or []
        conflict = False
        for pid in pids:
            existing = product_types.get(pid, set())
            for other in existing:
                if frozenset({atype, other}) in mutex:
                    conflict = True
                    break
            if conflict:
                break
        if conflict:
            continue
        kept.append(row)
        for pid in pids:
            product_types.setdefault(pid, set()).add(atype)

    return kept
