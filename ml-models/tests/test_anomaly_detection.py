import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from anomaly_detection.model import detect_anomalies


def test_detects_obvious_spike():
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    revenue = [1000] * 19 + [10000]  # last day is a huge spike
    df = pd.DataFrame({"date": dates, "revenue": revenue})
    anomalies = detect_anomalies(df, value_col="revenue")
    assert not anomalies.empty
    assert anomalies.iloc[0]["severity"] in ("high", "medium")


def test_stable_series_has_no_anomalies():
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    revenue = [1000 + (i % 3) for i in range(20)]  # tiny natural variation
    df = pd.DataFrame({"date": dates, "revenue": revenue})
    anomalies = detect_anomalies(df, value_col="revenue")
    assert anomalies.empty


def test_insufficient_history_returns_empty():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")  # below min_history_days
    df = pd.DataFrame({"date": dates, "revenue": [100, 200, 100, 5000, 100]})
    anomalies = detect_anomalies(df, value_col="revenue")
    assert anomalies.empty


def test_zero_variance_series_does_not_crash():
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    df = pd.DataFrame({"date": dates, "revenue": [1000] * 20})  # perfectly flat
    anomalies = detect_anomalies(df, value_col="revenue")
    assert anomalies.empty
