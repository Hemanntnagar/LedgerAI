"""
Runs basket analysis per-store, plus one global all-store run, and
writes qualifying rules to association_rules.
"""
import logging

from common.data_loaders import get_session, Base, engine, load_baskets, Store
from common.result_models import AssociationRule
from basket_analysis.model import compute_association_rules

logger = logging.getLogger("basket_analysis.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _write_rules(session, rules_df, store_id):
    count = 0
    for _, r in rules_df.iterrows():
        session.add(AssociationRule(
            store_id=store_id,
            antecedent_product_ids=list(r["antecedents"]),
            consequent_product_ids=list(r["consequents"]),
            support=float(r["support"]),
            confidence=float(r["confidence"]),
            lift=float(r["lift"]),
        ))
        count += 1
    return count


def run_basket_analysis_job() -> dict:
    Base.metadata.create_all(bind=engine)
    session = get_session()
    summary = {"global_rules": 0, "per_store_rules": {}}

    try:
        session.query(AssociationRule).delete()
        session.commit()

        # Global (all-store) run
        all_baskets = load_baskets(session)
        global_rules = compute_association_rules(all_baskets)
        summary["global_rules"] = _write_rules(session, global_rules, store_id=None)
        session.commit()
        logger.info(f"Global basket analysis: {summary['global_rules']} qualifying rules")

        # Per-store runs
        stores = session.query(Store).all()
        for store in stores:
            store_baskets = load_baskets(session, store_id=store.store_id)
            store_rules = compute_association_rules(store_baskets)
            n = _write_rules(session, store_rules, store_id=store.store_id)
            summary["per_store_rules"][store.store_name] = n
            session.commit()
            logger.info(f"Store '{store.store_name}': {n} qualifying rules")

    finally:
        session.close()

    logger.info(f"Basket analysis complete: {summary}")
    return summary


if __name__ == "__main__":
    run_basket_analysis_job()
