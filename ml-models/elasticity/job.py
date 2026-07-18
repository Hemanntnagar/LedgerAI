"""
Runs price elasticity estimation per (store, product) and writes
results to price_elasticity.
"""
import logging
from datetime import timedelta

from common.data_loaders import get_session, Base, engine, load_daily_product_sales
from common.result_models import PriceElasticity
from elasticity.model import estimate_elasticity

logger = logging.getLogger("elasticity.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_elasticity_job() -> dict:
    Base.metadata.create_all(bind=engine)
    session = get_session()
    summary = {"elastic": 0, "inelastic": 0, "unreliable_estimate": 0, "insufficient_price_variation": 0}

    try:
        session.query(PriceElasticity).delete()
        session.commit()

        all_sales = load_daily_product_sales(session)
        if all_sales.empty:
            logger.warning("No sales data found")
            return summary

        groups = all_sales.groupby(["store_id", "product_id"])
        for (store_id, product_id), group in groups:
            daily_series = (
                group.groupby("date", as_index=False)
                .agg(units=("units", "sum"), avg_unit_price=("avg_unit_price", "mean"))
                .sort_values("date")
            )
            result = estimate_elasticity(daily_series)
            period_start = daily_series["date"].min().date()
            period_end = daily_series["date"].max().date()

            session.add(PriceElasticity(
                store_id=store_id, product_id=product_id,
                elasticity_coefficient=result["elasticity_coefficient"],
                r_squared=result["r_squared"],
                classification=result["classification"],
                period_start=period_start, period_end=period_end,
            ))
            summary[result["classification"]] += 1

        session.commit()

    finally:
        session.close()

    logger.info(f"Elasticity job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_elasticity_job()
