"""
Inventory signals from sales proxies (Step 5): dead stock + velocity alerts.
True days-of-stock waits for an optional stock feed.
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
    list_stores,
    load_daily_product_sales,
    load_product_names,
)
from common.result_models import InventorySignal

logger = logging.getLogger("inventory.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def detect_inventory_signals(sales: pd.DataFrame, as_of: pd.Timestamp, config: dict, names: dict) -> list[dict]:
    if sales.empty:
        return []

    dead_days = config["dead_stock_days"]
    vel_window = config["velocity_window_days"]
    vel_base = config["velocity_baseline_days"]
    drop_pct = config["velocity_drop_pct"]

    signals = []
    for (store_id, product_id), group in sales.groupby(["store_id", "product_id"]):
        g = group.sort_values("date")
        last_sale = g["date"].max()
        days_since = (as_of - last_sale).days
        pname = names.get(product_id, product_id)

        if days_since >= dead_days:
            signals.append({
                "store_id": store_id,
                "product_id": product_id,
                "signal_type": "dead_stock",
                "days_since_sale": int(days_since),
                "recent_units_per_day": 0.0,
                "baseline_units_per_day": float(g["units"].sum() / max((g["date"].max() - g["date"].min()).days + 1, 1)),
                "severity": "high" if days_since >= dead_days * 1.5 else "medium",
                "suggestion_text": (
                    f"Dead stock: {pname} has no sales for {days_since} days — "
                    f"clearance or stop reordering"
                ),
            })
            continue

        recent_start = as_of - timedelta(days=vel_window)
        base_start = as_of - timedelta(days=vel_base)
        recent = g[g["date"] >= recent_start]["units"].sum() / max(vel_window, 1)
        baseline = g[(g["date"] >= base_start) & (g["date"] < recent_start)]["units"].sum() / max(vel_base - vel_window, 1)

        if baseline > 0 and recent < baseline * (1 - drop_pct / 100.0):
            signals.append({
                "store_id": store_id,
                "product_id": product_id,
                "signal_type": "velocity_drop",
                "days_since_sale": int(days_since),
                "recent_units_per_day": round(float(recent), 3),
                "baseline_units_per_day": round(float(baseline), 3),
                "severity": "medium",
                "suggestion_text": (
                    f"Velocity drop: {pname} selling {recent:.1f}/day vs baseline {baseline:.1f}/day — "
                    f"check stock position or demand shift"
                ),
            })
        elif baseline > 0 and recent > baseline * (1 + drop_pct / 100.0):
            signals.append({
                "store_id": store_id,
                "product_id": product_id,
                "signal_type": "velocity_surge",
                "days_since_sale": int(days_since),
                "recent_units_per_day": round(float(recent), 3),
                "baseline_units_per_day": round(float(baseline), 3),
                "severity": "low",
                "suggestion_text": (
                    f"Velocity surge: {pname} at {recent:.1f}/day vs {baseline:.1f}/day — "
                    f"consider deeper stock"
                ),
            })

    return signals


def run_inventory_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["inventory"]
    session = get_session()
    summary = {"signals": 0, "by_type": {}, "store_id": store_id}

    try:
        q = session.query(InventorySignal)
        if store_id:
            q = q.filter(InventorySignal.store_id == store_id)
        q.delete(synchronize_session=False)
        session.commit()

        sales = load_daily_product_sales(session, store_id=store_id)
        if sales.empty:
            logger.warning("No sales data for inventory signals")
            return summary

        as_of = sales["date"].max()
        names = load_product_names(session)
        signals = detect_inventory_signals(sales, as_of, config, names)

        for s in signals:
            session.add(InventorySignal(**s))
            summary["signals"] += 1
            summary["by_type"][s["signal_type"]] = summary["by_type"].get(s["signal_type"], 0) + 1

        session.commit()
        _ = list_stores(session, store_id=store_id)

    finally:
        session.close()

    logger.info(f"Inventory job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_inventory_job()
