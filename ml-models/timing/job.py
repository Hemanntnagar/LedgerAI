"""
Timing & operations (Step 5): festival/promo-day lift vs baseline + peak staffing hints.
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
    load_festivals,
    load_store_daily_revenue,
)
from common.result_models import TimingInsight

logger = logging.getLogger("timing.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _load_invoice_hours(session, store_id=None) -> pd.DataFrame:
    """Revenue by store × weekday × hour when invoice_time is present."""
    from db.models import Invoice

    q = session.query(
        Invoice.store_id,
        Invoice.invoice_date,
        Invoice.invoice_time,
        Invoice.grand_total,
    )
    if store_id:
        q = q.filter(Invoice.store_id == store_id)
    rows = q.all()
    if not rows:
        return pd.DataFrame(columns=["store_id", "date", "hour", "dow", "revenue"])

    records = []
    for r in rows:
        hour = None
        if r.invoice_time is not None:
            hour = r.invoice_time.hour if hasattr(r.invoice_time, "hour") else None
        records.append({
            "store_id": r.store_id,
            "date": r.invoice_date,
            "hour": hour,
            "dow": r.invoice_date.weekday() if r.invoice_date else None,
            "revenue": float(r.grand_total or 0),
        })
    return pd.DataFrame(records)


def run_timing_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["timing"]
    session = get_session()
    summary = {"promo_lifts": 0, "peak_hints": 0, "store_id": store_id}

    try:
        q = session.query(TimingInsight)
        if store_id:
            q = q.filter(TimingInsight.store_id == store_id)
        q.delete(synchronize_session=False)
        session.commit()

        store_rev = load_store_daily_revenue(session, store_id=store_id)
        festivals = load_festivals(session)
        stores = list_stores(session, store_id=store_id)
        hourly = _load_invoice_hours(session, store_id=store_id)

        if store_rev.empty:
            logger.warning("No revenue data for timing insights")
            return summary

        as_of = store_rev["date"].max()
        baseline_days = 28
        min_lift = config["promo_lift_min"]

        # Festival-day lift for past festivals overlapping sales history
        past_fests = festivals[festivals["end_date"] <= as_of.date()].sort_values("end_date", ascending=False).head(8)
        for store in stores:
            srev = store_rev[store_rev["store_id"] == store.store_id]
            for _, fest in past_fests.iterrows():
                mask = (srev["date"] >= pd.Timestamp(fest["start_date"])) & (
                    srev["date"] <= pd.Timestamp(fest["end_date"])
                )
                fest_part = srev.loc[mask]
                if fest_part.empty:
                    continue
                fest_daily = fest_part["revenue"].sum() / max(len(fest_part), 1)
                base_end = pd.Timestamp(fest["start_date"]) - timedelta(days=1)
                base_start = base_end - timedelta(days=baseline_days - 1)
                base_part = srev[(srev["date"] >= base_start) & (srev["date"] <= base_end)]
                if base_part.empty:
                    continue
                base_daily = base_part["revenue"].sum() / max(len(base_part), 1)
                if base_daily <= 0:
                    continue
                lift = fest_daily / base_daily
                if lift < min_lift:
                    continue
                session.add(TimingInsight(
                    store_id=store.store_id,
                    insight_type="promo_lift",
                    label=f"{fest['name']} {fest['year']}",
                    metric_value=round(float(lift), 3),
                    baseline_value=round(float(base_daily), 2),
                    suggestion_text=(
                        f"{fest['name']} lifted daily revenue to {lift:.0%} of baseline — "
                        f"staff and stock for next occurrence"
                    ),
                    evidence_json={"fest_daily": round(fest_daily, 2), "baseline_daily": round(base_daily, 2)},
                ))
                summary["promo_lifts"] += 1

            # Peak staffing by weekday
            srev = srev.copy()
            srev["dow"] = srev["date"].dt.dayofweek
            dow_rev = srev.groupby("dow")["revenue"].sum()
            total = dow_rev.sum() or 1
            dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            for dow, rev in dow_rev.items():
                share = float(rev) / float(total)
                if share >= config["peak_hour_share_min"]:
                    session.add(TimingInsight(
                        store_id=store.store_id,
                        insight_type="peak_staffing",
                        label=dow_names[int(dow)],
                        metric_value=round(share, 3),
                        baseline_value=round(1 / 7, 3),
                        suggestion_text=(
                            f"Peak day {dow_names[int(dow)]}: {share:.0%} of weekly revenue — "
                            f"roster extra counter staff"
                        ),
                        evidence_json={"weekday_revenue": round(float(rev), 2)},
                    ))
                    summary["peak_hints"] += 1

            # Peak hours if invoice_time available
            sh = hourly[(hourly["store_id"] == store.store_id) & hourly["hour"].notna()]
            if not sh.empty:
                by_hour = sh.groupby("hour")["revenue"].sum()
                htotal = by_hour.sum() or 1
                for hour, rev in by_hour.items():
                    share = float(rev) / float(htotal)
                    if share >= config["peak_hour_share_min"]:
                        session.add(TimingInsight(
                            store_id=store.store_id,
                            insight_type="peak_staffing",
                            label=f"{int(hour):02d}:00",
                            metric_value=round(share, 3),
                            baseline_value=None,
                            suggestion_text=(
                                f"Peak hour {int(hour):02d}:00 carries {share:.0%} of timed revenue — "
                                f"add staffing"
                            ),
                            evidence_json={"hour_revenue": round(float(rev), 2)},
                        ))
                        summary["peak_hints"] += 1

        session.commit()

    finally:
        session.close()

    logger.info(f"Timing job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_timing_job()
