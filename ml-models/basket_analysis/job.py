"""
Runs basket analysis per-store (and global when not store-scoped), and
writes qualifying rules to association_rules with Step 5 suggestion templates.
"""
import logging

from common.data_loaders import get_session, Base, engine, load_baskets, list_stores, load_product_names
from common.result_models import AssociationRule
from common.config import load_config
from basket_analysis.model import compute_association_rules

logger = logging.getLogger("basket_analysis.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _format_suggestion(antecedents, consequents, confidence, lift, names: dict) -> str:
    a = ", ".join(names.get(p, p) for p in antecedents)
    c = ", ".join(names.get(p, p) for p in consequents)
    pct = round(float(confidence) * 100)
    return (
        f"{pct}% of customers who buy {a} also buy {c} "
        f"(lift {float(lift):.2f}) — bundle for combo discount"
    )


def _write_rules(session, rules_df, store_id, names: dict, top_n: int) -> int:
    if rules_df.empty:
        return 0
    ranked = rules_df.sort_values("lift", ascending=False).head(top_n)
    count = 0
    for _, r in ranked.iterrows():
        ants = list(r["antecedents"])
        cons = list(r["consequents"])
        session.add(AssociationRule(
            store_id=store_id,
            antecedent_product_ids=ants,
            consequent_product_ids=cons,
            support=float(r["support"]),
            confidence=float(r["confidence"]),
            lift=float(r["lift"]),
            suggestion_text=_format_suggestion(ants, cons, r["confidence"], r["lift"], names),
        ))
        count += 1
    return count


def run_basket_analysis_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["basket_analysis"]
    top_n = config.get("top_n_suggestions", 20)
    session = get_session()
    summary = {"global_rules": 0, "per_store_rules": {}, "store_id": store_id}

    try:
        q = session.query(AssociationRule)
        if store_id:
            q = q.filter(AssociationRule.store_id == store_id)
        q.delete(synchronize_session=False)
        session.commit()

        names = load_product_names(session)

        if not store_id:
            all_baskets = load_baskets(session)
            global_rules = compute_association_rules(all_baskets)
            summary["global_rules"] = _write_rules(session, global_rules, None, names, top_n)
            session.commit()
            logger.info(f"Global basket analysis: {summary['global_rules']} qualifying rules")

        stores = list_stores(session, store_id=store_id)
        for store in stores:
            store_baskets = load_baskets(session, store_id=store.store_id)
            store_rules = compute_association_rules(store_baskets)
            n = _write_rules(session, store_rules, store.store_id, names, top_n)
            summary["per_store_rules"][store.store_name] = n
            session.commit()
            logger.info(f"Store '{store.store_name}': {n} qualifying rules")

    finally:
        session.close()

    logger.info(f"Basket analysis complete: {summary}")
    return summary


if __name__ == "__main__":
    run_basket_analysis_job()
