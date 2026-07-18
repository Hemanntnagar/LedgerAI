"""
Rolling z-score anomaly detection on daily revenue per (store, product)
and per-store aggregate. Simple and explainable -- swap for
IsolationForest later if this proves too noisy in practice.
"""
import pandas as pd
import numpy as np

from common.config import load_config


def detect_anomalies(daily_series: pd.DataFrame, value_col: str = "revenue") -> pd.DataFrame:
    """daily_series needs columns: date, <value_col>.
    Returns rows flagged as anomalies with z_score and severity."""
    config = load_config()["anomaly_detection"]
    threshold = config["z_score_threshold"]
    min_history = config["min_history_days"]

    if len(daily_series) < min_history:
        return pd.DataFrame()

    df = daily_series.sort_values("date").copy()
    mean = df[value_col].mean()
    std = df[value_col].std()

    if std == 0 or pd.isna(std):
        return pd.DataFrame()

    df["z_score"] = (df[value_col] - mean) / std
    anomalies = df[df["z_score"].abs() > threshold].copy()
    if anomalies.empty:
        return anomalies

    anomalies["expected_value"] = mean
    anomalies["severity"] = anomalies["z_score"].abs().apply(
        lambda z: "high" if z > threshold * 1.5 else "medium"
    )
    return anomalies
