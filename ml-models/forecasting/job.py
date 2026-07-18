"""
Orchestration for the demand forecasting job: pulls daily sales per
(store, product), forecasts each series, writes to forecast_results.

Idempotent: deletes existing forecast rows (scoped by store when set)
before inserting new ones.
"""
import logging

from common.data_loaders import get_session, Base, engine, load_daily_product_sales
from common.result_models import ForecastResult
from common.config import load_config
from forecasting.model import forecast_product, MODEL_VERSION

logger = logging.getLogger("forecasting.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_forecast_job(horizon_days: int = None, store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["forecasting"]
    grain = config.get("grain", "daily")
    session = get_session()
    summary = {
        "products_forecasted": 0,
        "products_skipped_no_data": 0,
        "low_confidence_count": 0,
        "medium_confidence_count": 0,
        "high_confidence_count": 0,
        "grain": grain,
        "store_id": store_id,
    }

    try:
        all_sales = load_daily_product_sales(session, store_id=store_id)
        if all_sales.empty:
            logger.warning("No sales data found -- nothing to forecast")
            return summary

        q = session.query(ForecastResult)
        if store_id:
            q = q.filter(ForecastResult.store_id == store_id)
        q.delete()
        session.commit()

        groups = all_sales.groupby(["store_id", "product_id"])
        for (sid, product_id), group in groups:
            daily_series = (
                group.groupby("date", as_index=False)
                .agg(units=("units", "sum"), revenue=("revenue", "sum"))
                .sort_values("date")
            )
            if daily_series.empty:
                summary["products_skipped_no_data"] += 1
                continue

            forecast_df = forecast_product(daily_series, horizon_days, grain=grain)
            for _, row in forecast_df.iterrows():
                result = ForecastResult(
                    store_id=sid,
                    product_id=product_id,
                    forecast_date=row["forecast_date"],
                    predicted_units=row["predicted_units"],
                    predicted_revenue=row["predicted_revenue"],
                    lower_bound=row["lower_bound"],
                    upper_bound=row["upper_bound"],
                    confidence_level=row["confidence_level"],
                    backtest_mape_pct=row.get("backtest_mape_pct"),
                    grain=row.get("grain", grain),
                    model_version=MODEL_VERSION,
                )
                session.add(result)
                conf = row["confidence_level"]
                if conf == "low":
                    summary["low_confidence_count"] += 1
                elif conf == "medium":
                    summary["medium_confidence_count"] += 1
                else:
                    summary["high_confidence_count"] += 1

            summary["products_forecasted"] += 1
            session.commit()
            logger.info(
                f"Forecasted store={sid} product={product_id}: "
                f"{len(forecast_df)} periods, confidence={forecast_df['confidence_level'].iloc[0]}"
            )

    finally:
        session.close()

    logger.info(f"Forecast job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_forecast_job()
