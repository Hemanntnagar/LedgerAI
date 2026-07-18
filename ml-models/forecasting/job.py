"""
Orchestration for the demand forecasting job: pulls daily sales per
(store, product), forecasts each series, writes to forecast_results.

Idempotent: deletes any existing forecast rows for this run's forecast
dates before inserting new ones.
"""
import logging
from datetime import date

from common.data_loaders import get_session, Base, engine
from common.data_loaders import load_daily_product_sales
from common.result_models import ForecastResult
from forecasting.model import forecast_product, MODEL_VERSION

logger = logging.getLogger("forecasting.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_forecast_job(horizon_days: int = None) -> dict:
    Base.metadata.create_all(bind=engine)  # ensure result tables exist
    session = get_session()
    summary = {"products_forecasted": 0, "products_skipped_no_data": 0, "low_confidence_count": 0, "high_confidence_count": 0}

    try:
        all_sales = load_daily_product_sales(session)
        if all_sales.empty:
            logger.warning("No sales data found -- nothing to forecast")
            return summary

        # Clear previous forecast results for a clean re-run (idempotency)
        session.query(ForecastResult).delete()
        session.commit()

        groups = all_sales.groupby(["store_id", "product_id"])
        for (store_id, product_id), group in groups:
            daily_series = (
                group.groupby("date", as_index=False)
                .agg(units=("units", "sum"), revenue=("revenue", "sum"))
                .sort_values("date")
            )
            if daily_series.empty:
                summary["products_skipped_no_data"] += 1
                continue

            forecast_df = forecast_product(daily_series, horizon_days)
            for _, row in forecast_df.iterrows():
                result = ForecastResult(
                    store_id=store_id,
                    product_id=product_id,
                    forecast_date=row["forecast_date"],
                    predicted_units=row["predicted_units"],
                    predicted_revenue=row["predicted_revenue"],
                    lower_bound=row["lower_bound"],
                    upper_bound=row["upper_bound"],
                    confidence_level=row["confidence_level"],
                    model_version=MODEL_VERSION,
                )
                session.add(result)
                if row["confidence_level"] == "low":
                    summary["low_confidence_count"] += 1
                else:
                    summary["high_confidence_count"] += 1

            summary["products_forecasted"] += 1
            session.commit()
            logger.info(f"Forecasted store={store_id} product={product_id}: "
                        f"{len(forecast_df)} days, confidence={forecast_df['confidence_level'].iloc[0]}")

    finally:
        session.close()

    logger.info(f"Forecast job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_forecast_job()
