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


def enrich_pricing_flags(daily_series: pd.DataFrame, elasticity: dict, product_meta, config: dict) -> dict:
    """
    Step 5: detect promo floor (price far below median) and margin-safe flag.
    product_meta: Series/dict with optional unit_cost, or None.
    """
    assume_margin = float(config.get("assume_margin_pct", 25))
    drop_pct = float(config.get("promo_floor_drop_pct", 15))

    prices = daily_series["avg_unit_price"].dropna()
    promo_floor = False
    if not prices.empty:
        median = float(prices.median())
        recent = float(prices.tail(7).mean()) if len(prices) else median
        if median > 0 and recent <= median * (1 - drop_pct / 100.0):
            promo_floor = True

    unit_cost = None
    if product_meta is not None:
        unit_cost = product_meta.get("unit_cost") if hasattr(product_meta, "get") else getattr(product_meta, "unit_cost", None)
        try:
            unit_cost = float(unit_cost) if unit_cost is not None and not (isinstance(unit_cost, float) and np.isnan(unit_cost)) else None
        except (TypeError, ValueError):
            unit_cost = None

    margin_safe = None
    if not prices.empty:
        price = float(prices.tail(7).mean())
        if unit_cost is not None and price > 0:
            margin_pct = (price - unit_cost) / price * 100
            margin_safe = margin_pct >= assume_margin
        else:
            # No cost: assume list price is margin-safe unless deep promo floor
            margin_safe = not promo_floor

    suggestion = None
    clf = elasticity.get("classification")
    if promo_floor and margin_safe is False:
        suggestion = "Stop discounting {product} — recent price looks below a safe margin floor"
    elif promo_floor:
        suggestion = "Review promo on {product} — price is ≥{drop}% below median".replace("{drop}", str(int(drop_pct)))
    elif clf == "elastic":
        suggestion = "Price cuts on {product} likely move volume (elastic) — prefer targeted offers over blanket cuts"
    elif clf == "inelastic":
        suggestion = "{product} looks inelastic — avoid unnecessary discounting"

    return {
        "promo_floor_flag": promo_floor,
        "margin_safe": margin_safe,
        "assumed_margin_pct": assume_margin,
        "suggestion_text": suggestion,
    }
