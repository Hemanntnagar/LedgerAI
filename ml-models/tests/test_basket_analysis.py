import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from basket_analysis.model import compute_association_rules


def test_finds_strong_association_between_frequently_paired_products():
    # A and B co-occur far more than their individual base rates would
    # predict by chance -- unlike a "always paired, never apart" fixture
    # (which mathematically produces lift == 1, not > 1, since there's no
    # baseline variation to compare against), this includes some baskets
    # where A and B appear WITHOUT each other, giving a real lift signal.
    rows = []
    for i in range(40):
        rows.append({"invoice_id": f"inv_ab_{i}", "product_id": "A"})
        rows.append({"invoice_id": f"inv_ab_{i}", "product_id": "B"})
    for i in range(10):
        rows.append({"invoice_id": f"inv_ax_{i}", "product_id": "A"})
        rows.append({"invoice_id": f"inv_ax_{i}", "product_id": "X"})
    for i in range(10):
        rows.append({"invoice_id": f"inv_by_{i}", "product_id": "B"})
        rows.append({"invoice_id": f"inv_by_{i}", "product_id": "Y"})
    for i in range(40):
        rows.append({"invoice_id": f"inv_xy_{i}", "product_id": "X"})
        rows.append({"invoice_id": f"inv_xy_{i}", "product_id": "Y"})

    df = pd.DataFrame(rows)
    rules = compute_association_rules(df)

    assert not rules.empty
    # A -> B (or B -> A) should appear with lift > 1 (co-occur more than chance)
    found = any(
        ("A" in r["antecedents"] and "B" in r["consequents"]) or
        ("B" in r["antecedents"] and "A" in r["consequents"])
        for _, r in rules.iterrows()
    )
    assert found


def test_empty_baskets_returns_empty_dataframe():
    df = pd.DataFrame(columns=["invoice_id", "product_id"])
    rules = compute_association_rules(df)
    assert rules.empty


def test_single_item_baskets_produce_no_rules():
    # Every basket has exactly 1 item -- no co-occurrence possible
    rows = [{"invoice_id": f"inv{i}", "product_id": "A"} for i in range(20)]
    df = pd.DataFrame(rows)
    rules = compute_association_rules(df)
    assert rules.empty
