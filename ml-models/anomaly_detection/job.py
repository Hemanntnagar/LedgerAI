"""
Runs anomaly detection at both the per-(store, product) level and the
store-aggregate level, writing flagged anomalies to the anomalies table.
"""
import logging

from common.data_loaders import get_session, Base, engine, load_daily_product_sales, Store
from common.result_models import Anomaly
from anomaly_detection.model import detect_anomalies

logger = logging.getLogger("anomaly_detection.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_anomaly_detection_job() -> dict:
    Base.metadata.create_all(bind=engine)
    session = get_session()
    summary = {"product_level_anomalies": 0, "store_level_anomalies": 0}

    try:
        session.query(Anomaly).delete()
        session.commit()

        all_sales = load_daily_product_sales(session)
        if all_sales.empty:
            logger.warning("No sales data found")
            return summary

        # Product-level anomalies
        groups = all_sales.groupby(["store_id", "product_id"])
        for (store_id, product_id), group in groups:
            daily_series = (
                group.groupby("date", as_index=False)
                .agg(revenue=("revenue", "sum"))
                .sort_values("date")
            )
            anomalies = detect_anomalies(daily_series, value_col="revenue")
            for _, row in anomalies.iterrows():
                session.add(Anomaly(
                    store_id=store_id, product_id=product_id,
                    anomaly_date=row["date"].date(), metric="revenue",
                    actual_value=round(row["revenue"], 2),
                    expected_value=round(row["expected_value"], 2),
                    z_score=round(row["z_score"], 3),
                    severity=row["severity"],
                ))
                summary["product_level_anomalies"] += 1
        session.commit()

        # Store-level anomalies (aggregate across all products per store)
        store_daily = (
            all_sales.groupby(["store_id", "date"], as_index=False)
            .agg(revenue=("revenue", "sum"))
        )
        for store_id, group in store_daily.groupby("store_id"):
            daily_series = group.sort_values("date")
            anomalies = detect_anomalies(daily_series, value_col="revenue")
            for _, row in anomalies.iterrows():
                session.add(Anomaly(
                    store_id=store_id, product_id=None,
                    anomaly_date=row["date"].date(), metric="revenue",
                    actual_value=round(row["revenue"], 2),
                    expected_value=round(row["expected_value"], 2),
                    z_score=round(row["z_score"], 3),
                    severity=row["severity"],
                ))
                summary["store_level_anomalies"] += 1
        session.commit()

    finally:
        session.close()

    logger.info(f"Anomaly detection complete: {summary}")
    return summary


if __name__ == "__main__":
    run_anomaly_detection_job()
