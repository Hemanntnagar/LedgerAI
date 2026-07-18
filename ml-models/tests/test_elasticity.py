import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from elasticity.model import estimate_elasticity


def test_insufficient_price_variation_flagged():
    # Same price every day -- can't estimate elasticity
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    df = pd.DataFrame({"date": dates, "units": [10] * 30, "avg_unit_price": [100.0] * 30})
    result = estimate_elasticity(df)
    assert result["classification"] == "insufficient_price_variation"
    assert result["elasticity_coefficient"] is None


def test_detects_elastic_product():
    # Strong inverse relationship: as price rises, units drop sharply
    np.random.seed(0)
    n = 60
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    prices = np.random.uniform(50, 150, n)
    # elasticity ~ -2: units = k * price^-2
    units = np.round(50000 / (prices ** 2)) + 1
    df = pd.DataFrame({"date": dates, "units": units, "avg_unit_price": prices})
    result = estimate_elasticity(df)
    assert result["classification"] == "elastic"
    assert result["elasticity_coefficient"] < -1.0


def test_no_real_relationship_flagged_unreliable():
    # Random noise, no true price-demand relationship
    np.random.seed(1)
    n = 40
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    prices = np.random.uniform(90, 110, n)
    units = np.random.uniform(8, 12, n)  # independent of price
    df = pd.DataFrame({"date": dates, "units": units, "avg_unit_price": prices})
    result = estimate_elasticity(df)
    assert result["classification"] in ("unreliable_estimate", "inelastic")
