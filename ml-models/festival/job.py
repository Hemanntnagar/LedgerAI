"""
Indian festival intelligence (Step 5): YoY / pre-festival lift + stock/offer proposals.
Uses festival_events seeded in Step 0.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from common.config import load_config
from common.data_loaders import (
    Base,
    engine,
    get_session,
    list_stores,
    load_daily_product_sales,
    load_festivals,
    load_product_names,
    load_store_daily_revenue,
)
from common.result_models import FestivalInsight
from db.seed_festivals import seed_festival_events

logger = logging.getLogger("festival.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _window_product_revenue(sales: pd.DataFrame, store_id: str, start, end) -> pd.Series:
    mask = (
        (sales["store_id"] == store_id)
        & (sales["date"] >= pd.Timestamp(start))
        & (sales["date"] <= pd.Timestamp(end))
    )
    return sales.loc[mask].groupby("product_id")["revenue"].sum()


def _avg_daily_revenue(store_rev: pd.DataFrame, store_id: str, start, end) -> float:
    mask = (
        (store_rev["store_id"] == store_id)
        & (store_rev["date"] >= pd.Timestamp(start))
        & (store_rev["date"] <= pd.Timestamp(end))
    )
    part = store_rev.loc[mask]
    if part.empty:
        return 0.0
    days = max((pd.Timestamp(end) - pd.Timestamp(start)).days + 1, 1)
    return float(part["revenue"].sum() / days)


def build_festival_insights(
    sales: pd.DataFrame,
    store_rev: pd.DataFrame,
    festivals: pd.DataFrame,
    store_ids: list[str],
    names: dict,
    config: dict,
    as_of: date,
) -> list[dict]:
    pre = config["pre_window_days"]
    baseline_days = config["baseline_days"]
    min_lift = config["min_lift"]
    top_n = config["top_n_products"]

    insights = []
    if festivals.empty:
        return insights

    # Upcoming festivals (or most recent if none ahead)
    upcoming = festivals[festivals["start_date"] >= as_of].sort_values("start_date")
    if upcoming.empty:
        upcoming = festivals.sort_values("start_date", ascending=False).head(3)
    else:
        upcoming = upcoming.head(5)

    for _, fest in upcoming.iterrows():
        f_start, f_end = fest["start_date"], fest["end_date"]
        # Historical same-name prior year for YoY
        prior = festivals[
            (festivals["name"] == fest["name"]) & (festivals["year"] == fest["year"] - 1)
        ]
        hist_start = prior.iloc[0]["start_date"] if not prior.empty else f_start.replace(year=f_start.year - 1) if hasattr(f_start, "replace") else f_start
        hist_end = prior.iloc[0]["end_date"] if not prior.empty else f_end.replace(year=f_end.year - 1) if hasattr(f_end, "replace") else f_end

        # Use historical festival window for lift if as_of is before this year's festival
        # (we may not have future sales yet)
        analysis_start, analysis_end = hist_start, hist_end
        # Prefer last completed occurrence of this festival name
        past = festivals[
            (festivals["name"] == fest["name"]) & (festivals["end_date"] < as_of)
        ].sort_values("end_date", ascending=False)
        if not past.empty:
            analysis_start = past.iloc[0]["start_date"]
            analysis_end = past.iloc[0]["end_date"]

        base_end = analysis_start - timedelta(days=1)
        base_start = base_end - timedelta(days=baseline_days - 1)

        for store_id in store_ids:
            fest_daily = _avg_daily_revenue(store_rev, store_id, analysis_start, analysis_end)
            base_daily = _avg_daily_revenue(store_rev, store_id, base_start, base_end)
            store_lift = (fest_daily / base_daily) if base_daily > 0 else None

            yoy_lift = None
            if not prior.empty and past.empty is False:
                # compare this analysis window to prior year if we used current historical
                prev_daily = _avg_daily_revenue(store_rev, store_id, hist_start, hist_end)
                if prev_daily > 0 and fest_daily > 0 and analysis_start != hist_start:
                    yoy_lift = fest_daily / prev_daily

            window_start = fest["start_date"] - timedelta(days=pre)
            window_end = fest["end_date"]

            # Store-level summary
            if store_lift and store_lift >= min_lift:
                action = "stock_up"
                text = (
                    f"Prep for {fest['name']} {fest['year']}: store revenue historically "
                    f"{store_lift:.0%} of baseline — stock up from {window_start}"
                )
            elif store_lift:
                action = "watch"
                text = (
                    f"{fest['name']} {fest['year']}: modest historical lift "
                    f"({store_lift:.0%} of baseline) — watch and light promo"
                )
            else:
                action = "watch"
                text = (
                    f"{fest['name']} {fest['year']} window {window_start}→{window_end} — "
                    f"insufficient history for lift; plan standard festival assortment"
                )

            insights.append({
                "store_id": store_id,
                "festival_id": fest["festival_id"],
                "festival_name": fest["name"],
                "festival_year": int(fest["year"]),
                "window_start": window_start,
                "window_end": window_end,
                "product_id": None,
                "lift_vs_baseline": round(float(store_lift), 3) if store_lift else None,
                "yoy_lift": round(float(yoy_lift), 3) if yoy_lift else None,
                "action": action,
                "suggestion_text": text,
                "evidence_json": {
                    "analysis_start": str(analysis_start),
                    "analysis_end": str(analysis_end),
                    "baseline_start": str(base_start),
                    "baseline_end": str(base_end),
                    "fest_daily_revenue": round(fest_daily, 2),
                    "baseline_daily_revenue": round(base_daily, 2),
                },
            })

            # Product-level stock/offer proposals
            fest_rev = _window_product_revenue(sales, store_id, analysis_start, analysis_end)
            base_rev = _window_product_revenue(sales, store_id, base_start, base_end)
            if fest_rev.empty:
                continue

            fest_days = max((pd.Timestamp(analysis_end) - pd.Timestamp(analysis_start)).days + 1, 1)
            base_days = max(baseline_days, 1)
            lifts = []
            for pid, rev in fest_rev.items():
                b = float(base_rev.get(pid, 0.0))
                fest_d = float(rev) / fest_days
                base_d = b / base_days
                lift = (fest_d / base_d) if base_d > 0 else (2.0 if fest_d > 0 else None)
                if lift and lift >= min_lift:
                    lifts.append((pid, lift, fest_d, base_d))

            lifts.sort(key=lambda x: x[1], reverse=True)
            for pid, lift, fest_d, base_d in lifts[:top_n]:
                pname = names.get(pid, pid)
                insights.append({
                    "store_id": store_id,
                    "festival_id": fest["festival_id"],
                    "festival_name": fest["name"],
                    "festival_year": int(fest["year"]),
                    "window_start": window_start,
                    "window_end": window_end,
                    "product_id": pid,
                    "lift_vs_baseline": round(float(lift), 3),
                    "yoy_lift": None,
                    "action": "stock_up" if lift >= 1.5 else "offer",
                    "suggestion_text": (
                        f"For {fest['name']}: stock up on {pname} "
                        f"(historically {lift:.0%} of baseline daily revenue) "
                        f"— consider a festival bundle offer"
                    ),
                    "evidence_json": {
                        "fest_daily_revenue": round(fest_d, 2),
                        "baseline_daily_revenue": round(base_d, 2),
                    },
                })

    return insights


def run_festival_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["festival"]
    session = get_session()
    summary = {"insights": 0, "festivals_covered": 0, "store_id": store_id}

    try:
        # Ensure calendar is seeded
        seed_festival_events(session)

        q = session.query(FestivalInsight)
        if store_id:
            q = q.filter(FestivalInsight.store_id == store_id)
        q.delete(synchronize_session=False)
        session.commit()

        festivals = load_festivals(session)
        sales = load_daily_product_sales(session, store_id=store_id)
        store_rev = load_store_daily_revenue(session, store_id=store_id)
        names = load_product_names(session)
        stores = list_stores(session, store_id=store_id)
        store_ids = [s.store_id for s in stores]

        if not store_ids:
            logger.warning("No stores found")
            return summary

        as_of = date.today()
        if not sales.empty:
            as_of = sales["date"].max().date()

        insights = build_festival_insights(
            sales, store_rev, festivals, store_ids, names, config, as_of
        )
        names_seen = set()
        for row in insights:
            session.add(FestivalInsight(**row))
            summary["insights"] += 1
            names_seen.add((row["festival_name"], row["festival_year"]))
        summary["festivals_covered"] = len(names_seen)
        session.commit()

    finally:
        session.close()

    logger.info(f"Festival job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_festival_job()
