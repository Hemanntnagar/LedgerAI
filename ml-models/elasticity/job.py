"""
Price elasticity estimation + Step 5 promo-floor / margin-safe flags.
"""
import logging

from common.data_loaders import get_session, Base, engine, load_daily_product_sales, load_product_meta, load_product_names
from common.result_models import PriceElasticity
from common.config import load_config
from elasticity.model import estimate_elasticity, enrich_pricing_flags

logger = logging.getLogger("elasticity.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_elasticity_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    session = get_session()
    summary = {
        "elastic": 0,
        "inelastic": 0,
        "unreliable_estimate": 0,
        "insufficient_price_variation": 0,
        "promo_floor_flags": 0,
        "store_id": store_id,
    }

    try:
        q = session.query(PriceElasticity)
        if store_id:
            q = q.filter(PriceElasticity.store_id == store_id)
        q.delete()
        session.commit()

        all_sales = load_daily_product_sales(session, store_id=store_id)
        if all_sales.empty:
            logger.warning("No sales data found")
            return summary

        meta = load_product_meta(session).set_index("product_id")
        names = load_product_names(session)
        config = load_config()["elasticity"]

        groups = all_sales.groupby(["store_id", "product_id"])
        for (sid, product_id), group in groups:
            daily_series = (
                group.groupby("date", as_index=False)
                .agg(units=("units", "sum"), avg_unit_price=("avg_unit_price", "mean"))
                .sort_values("date")
            )
            result = estimate_elasticity(daily_series)
            flags = enrich_pricing_flags(daily_series, result, meta.loc[product_id] if product_id in meta.index else None, config)
            period_start = daily_series["date"].min().date()
            period_end = daily_series["date"].max().date()
            pname = names.get(product_id, product_id)

            session.add(PriceElasticity(
                store_id=sid, product_id=product_id,
                elasticity_coefficient=result["elasticity_coefficient"],
                r_squared=result["r_squared"],
                classification=result["classification"],
                period_start=period_start, period_end=period_end,
                promo_floor_flag=flags["promo_floor_flag"],
                margin_safe=flags["margin_safe"],
                assumed_margin_pct=flags["assumed_margin_pct"],
                suggestion_text=flags["suggestion_text"] and flags["suggestion_text"].format(product=pname),
            ))
            summary[result["classification"]] = summary.get(result["classification"], 0) + 1
            if flags["promo_floor_flag"]:
                summary["promo_floor_flags"] += 1

        session.commit()

    finally:
        session.close()

    logger.info(f"Elasticity job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_elasticity_job()
