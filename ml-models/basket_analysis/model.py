"""
Market basket analysis via the Apriori algorithm (mlxtend).

Filters to rules with meaningful support and lift > 1 (lift <= 1 means
no real association -- the products just co-occur by chance given their
individual popularity).
"""
import pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules
from mlxtend.preprocessing import TransactionEncoder

from common.config import load_config


def compute_association_rules(basket_df: pd.DataFrame) -> pd.DataFrame:
    """basket_df must have columns: invoice_id, product_id.
    Returns a DataFrame with columns: antecedents, consequents, support,
    confidence, lift (as frozensets of product_ids for antecedents/consequents)."""
    config = load_config()["basket_analysis"]

    if basket_df.empty:
        return pd.DataFrame()

    baskets = basket_df.groupby("invoice_id")["product_id"].apply(list).tolist()
    # Basket analysis needs multi-item baskets to find co-occurrence
    baskets = [b for b in baskets if len(b) > 1]
    if not baskets:
        return pd.DataFrame()

    te = TransactionEncoder()
    te_array = te.fit(baskets).transform(baskets)
    onehot_df = pd.DataFrame(te_array, columns=te.columns_)

    frequent_itemsets = apriori(onehot_df, min_support=config["min_support"], use_colnames=True)
    if frequent_itemsets.empty:
        return pd.DataFrame()

    rules = association_rules(frequent_itemsets, metric="lift", min_threshold=config["min_lift"])
    rules = rules[rules["lift"] > config["min_lift"]]
    return rules
