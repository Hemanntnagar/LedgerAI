import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from forecasting.model import forecast_product, _naive_forecast


def _make_series(n_days, base_units=10, base_revenue=1000):
    dates = pd.date_range("2026-01-01", periods=n_days, freq="D")
    return pd.DataFrame({"date": dates, "units": [base_units] * n_days, "revenue": [base_revenue] * n_days})


def test_naive_fallback_used_for_sparse_history():
    series = _make_series(10)  # below min_history_days_for_prophet (30)
    result = forecast_product(series, horizon_days=7, grain="daily")
    assert len(result) == 7
    assert (result["confidence_level"] == "low").all()


def test_naive_forecast_uses_trailing_average():
    series = _make_series(14, base_units=5, base_revenue=500)
    result = _naive_forecast(series, horizon_days=5, grain="daily")
    assert len(result) == 5
    assert result["predicted_units"].iloc[0] == 5.0
    assert result["predicted_revenue"].iloc[0] == 500.0
    assert result["lower_bound"].iloc[0] < result["predicted_revenue"].iloc[0]
    assert result["upper_bound"].iloc[0] > result["predicted_revenue"].iloc[0]


def test_empty_series_does_not_crash():
    empty = pd.DataFrame(columns=["date", "units", "revenue"])
    result = forecast_product(empty, horizon_days=5, grain="daily")
    assert len(result) == 5
    assert (result["confidence_level"] == "low").all()


def test_prophet_used_with_sufficient_history():
    series = _make_series(60, base_units=10, base_revenue=1000)
    # Flat series → low MAPE → high confidence; force daily grain for assert length
    result = forecast_product(series, horizon_days=7, grain="daily", mape_pct=10.0, _skip_mape=True)
    assert len(result) == 7
    assert (result["confidence_level"] == "high").all()
    assert (result["predicted_revenue"] >= 0).all()


def test_high_mape_downgrades_confidence():
    series = _make_series(60, base_units=10, base_revenue=1000)
    result = forecast_product(series, horizon_days=7, grain="daily", mape_pct=55.0, _skip_mape=True)
    assert (result["confidence_level"] == "low").all()
    assert (result["backtest_mape_pct"] == 55.0).all()
