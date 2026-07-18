"""
Per (store, product) demand forecasting.

Uses Prophet when there's enough history; falls back to a simple
moving-average projection for sparse products rather than fitting
Prophet on too little data (which produces confident-looking garbage).
"""
import logging
import warnings
import pandas as pd

from common.config import load_config

logger = logging.getLogger("forecasting")
warnings.filterwarnings("ignore", module="prophet")
warnings.filterwarnings("ignore", module="cmdstanpy")

MODEL_VERSION = "prophet_v1_naive_fallback"


def _naive_forecast(daily_series: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    """Simple fallback: project the trailing 14-day average units/day
    forward flat, with a wide +/-50% band to reflect low confidence."""
    recent = daily_series.tail(14)
    avg_units = recent["units"].mean() if not recent.empty else 0
    avg_revenue = recent["revenue"].mean() if not recent.empty else 0
    last_date = daily_series["date"].max() if not daily_series.empty else pd.Timestamp.today()

    rows = []
    for i in range(1, horizon_days + 1):
        d = last_date + pd.Timedelta(days=i)
        rows.append({
            "forecast_date": d.date(),
            "predicted_units": round(avg_units, 2),
            "predicted_revenue": round(avg_revenue, 2),
            "lower_bound": round(avg_revenue * 0.5, 2),
            "upper_bound": round(avg_revenue * 1.5, 2),
            "confidence_level": "low",
        })
    return pd.DataFrame(rows)


def _prophet_forecast(daily_series: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    from prophet import Prophet

    prophet_df = daily_series.rename(columns={"date": "ds", "revenue": "y"})[["ds", "y"]]

    model = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=False)
    model.fit(prophet_df)

    future = model.make_future_dataframe(periods=horizon_days)
    forecast = model.predict(future)
    forecast_future = forecast.tail(horizon_days)

    # Estimate units proportionally to revenue's predicted change vs its
    # historical average (keeps units and revenue forecasts consistent
    # without fitting two separate Prophet models).
    avg_revenue = daily_series["revenue"].mean() or 1
    avg_units = daily_series["units"].mean() or 0
    units_per_revenue = avg_units / avg_revenue if avg_revenue else 0

    rows = []
    for _, r in forecast_future.iterrows():
        predicted_revenue = max(0, r["yhat"])
        rows.append({
            "forecast_date": r["ds"].date(),
            "predicted_units": round(predicted_revenue * units_per_revenue, 2),
            "predicted_revenue": round(predicted_revenue, 2),
            "lower_bound": round(max(0, r["yhat_lower"]), 2),
            "upper_bound": round(max(0, r["yhat_upper"]), 2),
            "confidence_level": "high",
        })
    return pd.DataFrame(rows)


def forecast_product(daily_series: pd.DataFrame, horizon_days: int = None) -> pd.DataFrame:
    """daily_series must have columns: date, units, revenue (one row per day,
    gaps allowed -- Prophet handles missing days fine)."""
    config = load_config()["forecasting"]
    horizon_days = horizon_days or config["horizon_days"]
    min_history = config["min_history_days_for_prophet"]

    n_days_history = (daily_series["date"].max() - daily_series["date"].min()).days + 1 if not daily_series.empty else 0

    if daily_series.empty or n_days_history < min_history:
        logger.info(f"Insufficient history ({n_days_history} days) -- using naive fallback forecast")
        return _naive_forecast(daily_series, horizon_days)

    try:
        return _prophet_forecast(daily_series, horizon_days)
    except Exception as e:
        logger.warning(f"Prophet failed ({e}), falling back to naive forecast")
        return _naive_forecast(daily_series, horizon_days)
