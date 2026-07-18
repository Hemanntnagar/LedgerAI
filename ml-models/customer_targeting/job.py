"""
Customer targeting (Step 5): RFM segments, VIP/dormant/churn, next-best-offer.

Requires phone-linked invoices (customer_id populated).
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd

from common.config import load_config
from common.data_loaders import (
    Base,
    engine,
    get_session,
    has_customer_data,
    list_stores,
    load_customer_baskets,
    load_customer_invoices,
    load_product_names,
)
from common.result_models import CustomerSegment, NextBestOffer

logger = logging.getLogger("customer_targeting.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _quintile_scores(series: pd.Series, ascending: bool = True) -> pd.Series:
    """1–5 scores; ascending=True means higher raw value → higher score."""
    if series.empty or series.nunique() < 2:
        return pd.Series([3] * len(series), index=series.index)
    try:
        ranks = series.rank(method="first", ascending=ascending)
        return pd.qcut(ranks, 5, labels=[1, 2, 3, 4, 5], duplicates="drop").astype(int)
    except ValueError:
        # Too few unique bins — fall back to rank percentile
        pct = series.rank(pct=True, ascending=ascending)
        return (pct * 4).round().astype(int) + 1


def compute_rfm(invoices: pd.DataFrame, as_of: pd.Timestamp, config: dict) -> pd.DataFrame:
    """Return one row per (store_id, customer_id) with RFM + segment + churn."""
    if invoices.empty:
        return pd.DataFrame()

    lookback_start = as_of - timedelta(days=config["lookback_days"])
    df = invoices[invoices["invoice_date"] >= lookback_start].copy()
    if df.empty:
        return pd.DataFrame()

    grouped = df.groupby(["store_id", "customer_id", "phone_normalized"], dropna=False)
    agg = grouped.agg(
        last_purchase=("invoice_date", "max"),
        frequency=("invoice_id", "nunique"),
        monetary=("grand_total", "sum"),
    ).reset_index()
    agg["recency_days"] = (as_of - agg["last_purchase"]).dt.days.clip(lower=0)

    # Scores within each store
    rows = []
    for store_id, part in agg.groupby("store_id"):
        part = part.copy()
        part["r_score"] = _quintile_scores(part["recency_days"], ascending=False)  # lower recency = better
        part["f_score"] = _quintile_scores(part["frequency"], ascending=True)
        part["m_score"] = _quintile_scores(part["monetary"], ascending=True)
        part["rfm_sum"] = part["r_score"] + part["f_score"] + part["m_score"]

        vip_cutoff = part["monetary"].quantile(1 - config["vip_top_pct"] / 100.0)
        dormant_days = config["dormant_days"]
        churn_days = config["churn_risk_days"]

        def _segment(row):
            if row["recency_days"] >= dormant_days:
                return "dormant"
            if row["monetary"] >= vip_cutoff and row["frequency"] >= 2:
                return "vip"
            if row["rfm_sum"] >= 12:
                return "loyal"
            if row["recency_days"] >= churn_days * 0.7:
                return "at_risk"
            return "regular"

        part["segment"] = part.apply(_segment, axis=1)
        # Churn score: 0–1, higher = more likely churned
        part["churn_score"] = (part["recency_days"] / max(churn_days, 1)).clip(0, 1).round(3)
        rows.append(part)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def compute_next_best_offers(
    baskets: pd.DataFrame,
    segments: pd.DataFrame,
    names: dict,
    config: dict,
) -> pd.DataFrame:
    """
    For each customer, suggest products frequently bought with their history
    that they haven't purchased yet (affinity from co-occurrence).
    """
    if baskets.empty or segments.empty:
        return pd.DataFrame()

    min_inv = config.get("min_invoices_for_nbo", 2)
    # Product popularity per store (fallback)
    store_pop = (
        baskets.groupby(["store_id", "product_id"])
        .size()
        .rename("cnt")
        .reset_index()
    )

    # Pair co-occurrence within invoices
    inv_products = baskets.groupby("invoice_id")["product_id"].apply(set)
    pair_counts: dict[tuple, int] = {}
    for products in inv_products:
        plist = list(products)
        for i, a in enumerate(plist):
            for b in plist[i + 1 :]:
                pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1
                pair_counts[(b, a)] = pair_counts.get((b, a), 0) + 1

    cust_products = baskets.groupby(["store_id", "customer_id"])["product_id"].apply(set)
    cust_invoice_n = baskets.groupby(["store_id", "customer_id"])["invoice_id"].nunique()

    offers = []
    for _, seg in segments.iterrows():
        key = (seg["store_id"], seg["customer_id"])
        owned = cust_products.get(key, set())
        if cust_invoice_n.get(key, 0) < min_inv and seg["segment"] not in ("vip", "dormant", "at_risk"):
            continue

        scores: dict[str, float] = {}
        for p in owned:
            for (a, b), cnt in pair_counts.items():
                if a == p and b not in owned:
                    scores[b] = scores.get(b, 0) + cnt

        if not scores:
            # Fallback: store popular products not yet bought
            pop = store_pop[store_pop["store_id"] == seg["store_id"]].sort_values("cnt", ascending=False)
            for _, row in pop.head(5).iterrows():
                if row["product_id"] not in owned:
                    scores[row["product_id"]] = float(row["cnt"])

        if not scores:
            continue

        best_pid, best_score = max(scores.items(), key=lambda x: x[1])
        pname = names.get(best_pid, best_pid)
        if seg["segment"] == "dormant":
            reason = "win_back"
            text = f"Win-back: offer {pname} — customer dormant {int(seg['recency_days'])} days"
        elif seg["segment"] == "vip":
            reason = "vip_upsell"
            text = f"VIP next-best: suggest {pname} based on basket affinity"
        else:
            reason = "affinity"
            text = f"Customers like this often add {pname} — suggest at checkout"

        offers.append({
            "store_id": seg["store_id"],
            "customer_id": seg["customer_id"],
            "product_id": best_pid,
            "score": float(best_score),
            "reason": reason,
            "suggestion_text": text,
        })

    return pd.DataFrame(offers)


def run_customer_targeting_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["customer_targeting"]
    session = get_session()
    summary = {"segments": 0, "offers": 0, "skipped_no_customers": False, "store_id": store_id}

    try:
        if not has_customer_data(session, store_id=store_id):
            logger.warning("No phone-linked invoices — skipping customer targeting")
            summary["skipped_no_customers"] = True
            return summary

        q1 = session.query(CustomerSegment)
        q2 = session.query(NextBestOffer)
        if store_id:
            q1 = q1.filter(CustomerSegment.store_id == store_id)
            q2 = q2.filter(NextBestOffer.store_id == store_id)
        q1.delete(synchronize_session=False)
        q2.delete(synchronize_session=False)
        session.commit()

        invoices = load_customer_invoices(session, store_id=store_id)
        if invoices.empty:
            summary["skipped_no_customers"] = True
            return summary

        as_of = invoices["invoice_date"].max()
        segments = compute_rfm(invoices, as_of, config)
        names = load_product_names(session)
        baskets = load_customer_baskets(session, store_id=store_id)
        offers = compute_next_best_offers(baskets, segments, names, config)

        for _, row in segments.iterrows():
            session.add(CustomerSegment(
                store_id=row["store_id"],
                customer_id=row["customer_id"],
                phone_normalized=row.get("phone_normalized"),
                recency_days=int(row["recency_days"]),
                frequency=int(row["frequency"]),
                monetary=round(float(row["monetary"]), 2),
                r_score=int(row["r_score"]),
                f_score=int(row["f_score"]),
                m_score=int(row["m_score"]),
                segment=row["segment"],
                churn_score=float(row["churn_score"]),
            ))
            summary["segments"] += 1

        for _, row in offers.iterrows():
            session.add(NextBestOffer(
                store_id=row["store_id"],
                customer_id=row["customer_id"],
                product_id=row["product_id"],
                score=float(row["score"]),
                reason=row["reason"],
                suggestion_text=row["suggestion_text"],
            ))
            summary["offers"] += 1

        session.commit()
        # Ensure we touched listed stores for logging
        _ = list_stores(session, store_id=store_id)

    finally:
        session.close()

    logger.info(f"Customer targeting complete: {summary}")
    return summary


if __name__ == "__main__":
    run_customer_targeting_job()
