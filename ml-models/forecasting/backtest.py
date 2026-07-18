"""
Backtests the forecasting model: train on data up to a cutoff date,
forecast forward, compare to what actually happened, report MAPE.

This answers "is this model actually any good" -- separate from the
production job, which just generates forward-looking forecasts.
"""
import logging
import pandas as pd
import numpy as np

from common.data_loaders import get_session, load_daily_product_sales, load_product_names
from forecasting.model import forecast_product

logger = logging.getLogger("forecasting.backtest")


def backtest(cutoff_date: str, horizon_days: int = 14) -> pd.DataFrame:
    """Returns a DataFrame with one row per (store, product): MAPE and
    sample size, so you can see which products the model handles well
    vs. poorly."""
    session = get_session()
    try:
        all_sales = load_daily_product_sales(session)
        product_names = load_product_names(session)
    finally:
        session.close()

    if all_sales.empty:
        logger.warning("No data available for backtesting")
        return pd.DataFrame()

    cutoff = pd.Timestamp(cutoff_date)
    results = []

    groups = all_sales.groupby(["store_id", "product_id"])
    for (store_id, product_id), group in groups:
        daily_series = (
            group.groupby("date", as_index=False)
            .agg(units=("units", "sum"), revenue=("revenue", "sum"))
            .sort_values("date")
        )
        train = daily_series[daily_series["date"] <= cutoff]
        actual_future = daily_series[
            (daily_series["date"] > cutoff) & (daily_series["date"] <= cutoff + pd.Timedelta(days=horizon_days))
        ]

        if train.empty or actual_future.empty:
            continue

        forecast_df = forecast_product(train, horizon_days=horizon_days)
        forecast_df["forecast_date"] = pd.to_datetime(forecast_df["forecast_date"])

        merged = actual_future.merge(
            forecast_df, left_on="date", right_on="forecast_date", how="inner"
        )
        if merged.empty:
            continue

        # MAPE, guarding against division by zero on zero-revenue days
        nonzero = merged[merged["revenue"] > 0]
        if nonzero.empty:
            mape = None
        else:
            mape = float(np.mean(np.abs((nonzero["revenue"] - nonzero["predicted_revenue"]) / nonzero["revenue"])) * 100)

        results.append({
            "store_id": store_id,
            "product_id": product_id,
            "product_name": product_names.get(product_id, "Unknown"),
            "mape_pct": round(mape, 2) if mape is not None else None,
            "days_evaluated": len(merged),
            "confidence_level": forecast_df["confidence_level"].iloc[0] if not forecast_df.empty else None,
        })

    return pd.DataFrame(results).sort_values("mape_pct", na_position="last")


if __name__ == "__main__":
    import sys
    cutoff = sys.argv[1] if len(sys.argv) > 1 else "2026-06-01"
    df = backtest(cutoff)
    print(df.to_string(index=False))
    if not df.empty:
        valid = df["mape_pct"].dropna()
        if not valid.empty:
            print(f"\nAverage MAPE across products: {valid.mean():.2f}%")
