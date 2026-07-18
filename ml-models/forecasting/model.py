"""
Per (store, product) demand forecasting.

Uses Prophet when there's enough history; falls back to a simple
moving-average projection for sparse products rather than fitting
Prophet on too little data (which produces confident-looking garbage).

Step 5: optional weekly grain; confidence_level derived from backtest MAPE
when provided (not merely "did Prophet run").
"""
import logging
import warnings
import pandas as pd
import numpy as np

from common.config import load_config

logger = logging.getLogger("forecasting")
warnings.filterwarnings("ignore", module="prophet")
warnings.filterwarnings("ignore", module="cmdstanpy")

MODEL_VERSION = "prophet_v2_mape_confidence"


def confidence_from_mape(mape_pct: float | None) -> str:
    """Map backtest MAPE → high | medium | low."""
    config = load_config()["forecasting"]
    if mape_pct is None:
        return "low"
    if mape_pct <= config["mape_high_max_pct"]:
        return "high"
    if mape_pct <= config["mape_medium_max_pct"]:
        return "medium"
    return "low"


def to_weekly_series(daily_series: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily date/units/revenue to week-ending Sunday."""
    if daily_series.empty:
        return daily_series.copy()
    df = daily_series.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    weekly = df.resample("W-SUN").agg({"units": "sum", "revenue": "sum"}).dropna(how="all")
    weekly = weekly[weekly["units"].fillna(0) + weekly["revenue"].fillna(0) > 0]
    weekly = weekly.reset_index()
    return weekly


def _naive_forecast(daily_series: pd.DataFrame, horizon_days: int, *, grain: str = "daily") -> pd.DataFrame:
    """Simple fallback: project the trailing average forward flat."""
    window = 4 if grain == "weekly" else 14
    recent = daily_series.tail(window)
    avg_units = recent["units"].mean() if not recent.empty else 0
    avg_revenue = recent["revenue"].mean() if not recent.empty else 0
    last_date = daily_series["date"].max() if not daily_series.empty else pd.Timestamp.today()
    step = 7 if grain == "weekly" else 1
    n_steps = max(1, (horizon_days + step - 1) // step) if grain == "weekly" else horizon_days

    rows = []
    for i in range(1, n_steps + 1):
        d = last_date + pd.Timedelta(days=step * i)
        rows.append({
            "forecast_date": d.date() if hasattr(d, "date") else d,
            "predicted_units": round(float(avg_units), 2),
            "predicted_revenue": round(float(avg_revenue), 2),
            "lower_bound": round(float(avg_revenue) * 0.5, 2),
            "upper_bound": round(float(avg_revenue) * 1.5, 2),
            "confidence_level": "low",
            "backtest_mape_pct": None,
            "grain": grain,
        })
    return pd.DataFrame(rows)


def _prophet_forecast(
    series: pd.DataFrame,
    horizon_days: int,
    *,
    grain: str = "daily",
    mape_pct: float | None = None,
) -> pd.DataFrame:
    from prophet import Prophet

    prophet_df = series.rename(columns={"date": "ds", "revenue": "y"})[["ds", "y"]]
    model = Prophet(
        daily_seasonality=False,
        weekly_seasonality=(grain == "daily"),
        yearly_seasonality=False,
    )
    model.fit(prophet_df)

    step = 7 if grain == "weekly" else 1
    n_steps = max(1, (horizon_days + step - 1) // step) if grain == "weekly" else horizon_days
    freq = "W" if grain == "weekly" else "D"
    future = model.make_future_dataframe(periods=n_steps, freq=freq)
    forecast = model.predict(future)
    forecast_future = forecast.tail(n_steps)

    avg_revenue = series["revenue"].mean() or 1
    avg_units = series["units"].mean() or 0
    units_per_revenue = avg_units / avg_revenue if avg_revenue else 0

    conf = confidence_from_mape(mape_pct) if mape_pct is not None else "high"

    rows = []
    for _, r in forecast_future.iterrows():
        predicted_revenue = max(0, r["yhat"])
        rows.append({
            "forecast_date": r["ds"].date(),
            "predicted_units": round(predicted_revenue * units_per_revenue, 2),
            "predicted_revenue": round(predicted_revenue, 2),
            "lower_bound": round(max(0, r["yhat_lower"]), 2),
            "upper_bound": round(max(0, r["yhat_upper"]), 2),
            "confidence_level": conf,
            "backtest_mape_pct": round(float(mape_pct), 2) if mape_pct is not None else None,
            "grain": grain,
        })
    return pd.DataFrame(rows)


def compute_series_mape(
    series: pd.DataFrame,
    *,
    horizon_days: int,
    grain: str = "daily",
) -> float | None:
    """Hold out the last horizon window, forecast, return MAPE % on revenue."""
    if series.empty or len(series) < 5:
        return None
    series = series.sort_values("date").copy()
    series["date"] = pd.to_datetime(series["date"])
    step = 7 if grain == "weekly" else 1
    holdout_n = max(1, (horizon_days + step - 1) // step) if grain == "weekly" else horizon_days
    if len(series) <= holdout_n + 2:
        return None

    train = series.iloc[:-holdout_n]
    actual = series.iloc[-holdout_n:]
    try:
        # Avoid recursive MAPE when computing MAPE — use naive confidence path
        fc = forecast_product(train, horizon_days=horizon_days, grain=grain, mape_pct=None, _skip_mape=True)
    except Exception:
        return None
    if fc.empty:
        return None

    fc = fc.copy()
    fc["forecast_date"] = pd.to_datetime(fc["forecast_date"])
    actual = actual.copy()
    actual["date"] = pd.to_datetime(actual["date"])
    merged = actual.merge(fc, left_on="date", right_on="forecast_date", how="inner")
    nonzero = merged[merged["revenue"] > 0]
    if nonzero.empty:
        return None
    return float(np.mean(np.abs((nonzero["revenue"] - nonzero["predicted_revenue"]) / nonzero["revenue"])) * 100)


def forecast_product(
    daily_series: pd.DataFrame,
    horizon_days: int = None,
    *,
    grain: str = None,
    mape_pct: float | None = None,
    _skip_mape: bool = False,
) -> pd.DataFrame:
    """daily_series must have columns: date, units, revenue (one row per day,
    gaps allowed -- Prophet handles missing days fine).

    If mape_pct is None and _skip_mape is False, computes an internal holdout MAPE
    to set confidence (Step 5). Pass mape_pct explicitly to override.
    """
    config = load_config()["forecasting"]
    horizon_days = horizon_days or config["horizon_days"]
    grain = grain or config.get("grain", "daily")
    min_history = config["min_history_days_for_prophet"]

    series = daily_series.copy()
    if not series.empty:
        series["date"] = pd.to_datetime(series["date"])
        series = series.sort_values("date")

    if grain == "weekly" and not series.empty:
        series = to_weekly_series(series)
        # Weekly history threshold: ~4 weeks ≈ 30 days
        min_history_points = max(4, min_history // 7)
    else:
        min_history_points = min_history

    n_history = len(series) if grain == "weekly" else (
        (series["date"].max() - series["date"].min()).days + 1 if not series.empty else 0
    )

    if series.empty or n_history < min_history_points:
        logger.info(f"Insufficient history ({n_history}) -- using naive fallback forecast")
        return _naive_forecast(series, horizon_days, grain=grain)

    if mape_pct is None and not _skip_mape:
        mape_pct = compute_series_mape(
            series,
            horizon_days=config.get("backtest_horizon_days", 14),
            grain=grain,
        )

    try:
        return _prophet_forecast(series, horizon_days, grain=grain, mape_pct=mape_pct)
    except Exception as e:
        logger.warning(f"Prophet failed ({e}), falling back to naive forecast")
        return _naive_forecast(series, horizon_days, grain=grain)
