"""
Store benchmarking (Step 5): peer-normalized underperformer / best-practice flags.
When only one store exists, writes in_line summaries rather than peer flags.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import func

from common.config import load_config
from common.data_loaders import Base, engine, get_session, list_stores
from common.result_models import StoreBenchmark
from db.models import Invoice

logger = logging.getLogger("benchmarking.job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def run_benchmarking_job(store_id: str = None) -> dict:
    Base.metadata.create_all(bind=engine)
    config = load_config()["store_benchmarking"]
    session = get_session()
    summary = {"flags": 0, "store_id": store_id}

    try:
        q = session.query(StoreBenchmark)
        if store_id:
            q = q.filter(StoreBenchmark.store_id == store_id)
        q.delete(synchronize_session=False)
        session.commit()

        period_end = session.query(func.max(Invoice.invoice_date)).scalar()
        if not period_end:
            logger.warning("No invoices for benchmarking")
            return summary
        period_start = period_end - timedelta(days=30)

        rows = (
            session.query(
                Invoice.store_id,
                func.sum(Invoice.grand_total).label("revenue"),
                func.count(Invoice.invoice_id).label("txn_count"),
                func.avg(Invoice.grand_total).label("avg_basket"),
            )
            .filter(Invoice.invoice_date.between(period_start, period_end))
            .group_by(Invoice.store_id)
            .all()
        )
        if not rows:
            return summary

        df = pd.DataFrame(
            [
                {
                    "store_id": r.store_id,
                    "revenue": float(r.revenue or 0),
                    "txn_count": int(r.txn_count or 0),
                    "avg_basket": float(r.avg_basket or 0),
                }
                for r in rows
            ]
        )

        stores = list_stores(session, store_id=None)
        n_peers = len(stores)
        under_pct = config["underperform_pct"]
        min_peers = config["min_peer_stores"]

        metrics = ["revenue", "avg_basket", "txn_count"]
        medians = {m: float(df[m].median()) for m in metrics}

        target_ids = [store_id] if store_id else list(df["store_id"])
        for sid in target_ids:
            row = df[df["store_id"] == sid]
            if row.empty:
                continue
            row = row.iloc[0]
            for metric in metrics:
                val = float(row[metric])
                peer = medians[metric]
                pct = ((val - peer) / peer * 100) if peer else 0.0

                if n_peers < min_peers:
                    flag = "in_line"
                    text = f"Only {n_peers} store(s) — peer benchmarking limited for {metric}"
                elif pct <= -under_pct:
                    flag = "underperformer"
                    text = (
                        f"Underperformer on {metric}: {pct:.0f}% vs peer median — "
                        f"review best-practice stores"
                    )
                elif pct >= under_pct:
                    flag = "best_practice"
                    text = (
                        f"Best practice on {metric}: {pct:.0f}% above peer median — "
                        f"document what works here"
                    )
                else:
                    flag = "in_line"
                    text = f"{metric} within {under_pct}% of peer median"

                session.add(StoreBenchmark(
                    store_id=sid,
                    metric=metric,
                    store_value=round(val, 2),
                    peer_median=round(peer, 2),
                    pct_vs_peer=round(pct, 2),
                    flag=flag,
                    suggestion_text=text,
                    period_start=period_start,
                    period_end=period_end,
                ))
                summary["flags"] += 1

        session.commit()

    finally:
        session.close()

    logger.info(f"Benchmarking job complete: {summary}")
    return summary


if __name__ == "__main__":
    run_benchmarking_job()
