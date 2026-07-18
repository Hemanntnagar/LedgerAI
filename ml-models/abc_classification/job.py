"""
ABC classification: ranks products by revenue over a trailing period and
classifies into A (top revenue drivers) / B (moderate) / C (long tail)
based on cumulative revenue share. Deterministic ranking, no ML model.
"""
import logging
from datetime import date, timedelta

from common.data_loaders import get_session, Base, engine, Store, InvoiceLineItem, Invoice, Product
from common.result_models import ABCClassification
from common.config import load_config
from sqlalchemy import func

logger = logging.getLogger("abc_classification.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _classify_products(session, store_id, period_start, period_end, config):
    q = (
        session.query(
            Product.product_id,
            func.sum(InvoiceLineItem.line_total).label("revenue"),
        )
        .join(InvoiceLineItem, InvoiceLineItem.product_id == Product.product_id)
        .join(Invoice, Invoice.invoice_id == InvoiceLineItem.invoice_id)
        .filter(Invoice.invoice_date.between(period_start, period_end))
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    q = q.group_by(Product.product_id)
    rows = sorted(q.all(), key=lambda r: float(r.revenue or 0), reverse=True)

    total_revenue = sum(float(r.revenue or 0) for r in rows) or 1
    cumulative = 0.0
    results = []
    for i, r in enumerate(rows):
        rev = float(r.revenue or 0)
        cumulative += rev
        cumulative_pct = cumulative / total_revenue * 100
        if cumulative_pct <= config["class_a_cutoff_pct"]:
            product_class = "A"
        elif cumulative_pct <= config["class_b_cutoff_pct"]:
            product_class = "B"
        else:
            product_class = "C"
        results.append({
            "product_id": r.product_id,
            "revenue_rank": i + 1,
            "cumulative_pct": round(cumulative_pct, 2),
            "product_class": product_class,
        })
    return results


def run_abc_classification_job() -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["abc_classification"]
    session = get_session()
    summary = {"global": {"A": 0, "B": 0, "C": 0}, "per_store": {}}

    try:
        session.query(ABCClassification).delete()
        session.commit()

        period_end = session.query(func.max(Invoice.invoice_date)).scalar()
        if not period_end:
            logger.warning("No invoice data found")
            return summary
        period_start = period_end - timedelta(days=config["period_days"])

        # Global classification (store_id=None)
        results = _classify_products(session, None, period_start, period_end, config)
        for r in results:
            session.add(ABCClassification(
                store_id=None, product_id=r["product_id"],
                period_start=period_start, period_end=period_end,
                revenue_rank=r["revenue_rank"], cumulative_pct=r["cumulative_pct"],
                product_class=r["product_class"],
            ))
            summary["global"][r["product_class"]] += 1
        session.commit()

        # Per-store classification
        stores = session.query(Store).all()
        for store in stores:
            store_results = _classify_products(session, store.store_id, period_start, period_end, config)
            counts = {"A": 0, "B": 0, "C": 0}
            for r in store_results:
                session.add(ABCClassification(
                    store_id=store.store_id, product_id=r["product_id"],
                    period_start=period_start, period_end=period_end,
                    revenue_rank=r["revenue_rank"], cumulative_pct=r["cumulative_pct"],
                    product_class=r["product_class"],
                ))
                counts[r["product_class"]] += 1
            summary["per_store"][store.store_name] = counts
            session.commit()

    finally:
        session.close()

    logger.info(f"ABC classification complete: {summary}")
    return summary


if __name__ == "__main__":
    run_abc_classification_job()
