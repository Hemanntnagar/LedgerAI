"""
Price elasticity of demand via log-log regression:
    log(units) ~ log(price) + day_of_week dummies

The coefficient on log(price) is the elasticity estimate. Requires
actual historical price variation -- if a product's price never
changed, elasticity can't be estimated from observational data and
we say so rather than guessing.
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

from common.config import load_config


def estimate_elasticity(daily_series: pd.DataFrame) -> dict:
    """daily_series needs columns: date, units, avg_unit_price.
    Returns dict with elasticity_coefficient, r_squared, classification."""
    config = load_config()["elasticity"]

    df = daily_series[(daily_series["units"] > 0) & (daily_series["avg_unit_price"] > 0)].copy()
    n_price_points = df["avg_unit_price"].round(2).nunique()

    if n_price_points < config["min_price_points_required"]:
        return {
            "elasticity_coefficient": None,
            "r_squared": None,
            "classification": "insufficient_price_variation",
        }

    df["log_units"] = np.log(df["units"])
    df["log_price"] = np.log(df["avg_unit_price"])
    df["dow"] = pd.to_datetime(df["date"]).dt.dayofweek
    dow_dummies = pd.get_dummies(df["dow"], prefix="dow", drop_first=True)

    X = pd.concat([df[["log_price"]], dow_dummies], axis=1).astype(float)
    X = sm.add_constant(X)
    y = df["log_units"].astype(float)

    try:
        model = sm.OLS(y, X).fit()
    except Exception:
        return {
            "elasticity_coefficient": None,
            "r_squared": None,
            "classification": "unreliable_estimate",
        }

    coef = model.params.get("log_price")
    pvalue = model.pvalues.get("log_price")
    r_squared = model.rsquared

    if coef is None or pvalue is None or pvalue > 0.1:
        classification = "unreliable_estimate"
    elif coef < config["elastic_threshold"]:
        classification = "elastic"
    else:
        classification = "inelastic"

    return {
        "elasticity_coefficient": round(float(coef), 4) if coef is not None else None,
        "r_squared": round(float(r_squared), 4),
        "classification": classification,
    }
