"""
Step 9 — Monitoring: ingestion health + forecast MAPE / confidence drift.

Creates MonitoringAlert rows when thresholds in config/monitoring.yaml trip.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Optional

import yaml

from db.models import IngestionError, Invoice, MonitoringAlert, ReportSnapshot, Subscription


def _config_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "monitoring.yaml",
    )


def load_monitoring_config() -> dict:
    path = _config_path()
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _open_alert_exists(
    session,
    alert_type: str,
    *,
    store_id: Optional[str] = None,
) -> bool:
    q = session.query(MonitoringAlert).filter_by(
        alert_type=alert_type, status="open"
    )
    if store_id is None:
        q = q.filter(MonitoringAlert.store_id.is_(None))
    else:
        q = q.filter(MonitoringAlert.store_id == store_id)
    return q.first() is not None


def _raise_alert(
    session,
    *,
    alert_type: str,
    title: str,
    message: str,
    severity: str = "warning",
    store_id: Optional[str] = None,
    detail: Optional[dict] = None,
    dedupe: bool = True,
) -> Optional[MonitoringAlert]:
    if dedupe and _open_alert_exists(session, alert_type, store_id=store_id):
        return None
    row = MonitoringAlert(
        store_id=store_id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        detail_json=detail,
        status="open",
        created_at=datetime.utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def check_ingestion_health(
    session,
    *,
    window_hours: Optional[int] = None,
    raise_alerts: bool = True,
) -> dict[str, Any]:
    """
    Platform-wide ingestion health over a sliding window.
    error_rate = errors / (errors + successful invoices ingested).
    """
    cfg = load_monitoring_config().get("ingestion", {})
    hours = window_hours if window_hours is not None else int(cfg.get("window_hours", 24))
    since = datetime.utcnow() - timedelta(hours=hours)

    n_errors = (
        session.query(IngestionError)
        .filter(IngestionError.created_at >= since)
        .count()
    )
    n_ok = (
        session.query(Invoice)
        .filter(Invoice.ingested_at >= since)
        .count()
    )
    total = n_errors + n_ok
    error_rate = (n_errors / total) if total else 0.0

    by_type: dict[str, int] = {}
    for err in (
        session.query(IngestionError)
        .filter(IngestionError.created_at >= since)
        .all()
    ):
        by_type[err.error_type] = by_type.get(err.error_type, 0) + 1

    result = {
        "window_hours": hours,
        "since": since.isoformat() + "Z",
        "n_errors": n_errors,
        "n_invoices_ok": n_ok,
        "n_events": total,
        "error_rate": round(error_rate, 4),
        "by_error_type": by_type,
        "healthy": True,
        "alert_raised": False,
    }

    rate_max = float(cfg.get("error_rate_max", 0.15))
    min_errors = int(cfg.get("min_errors_to_alert", 5))
    min_events = int(cfg.get("min_events", 5))

    trip = (
        total >= min_events
        and n_errors >= min_errors
        and error_rate > rate_max
    )
    result["healthy"] = not trip
    result["thresholds"] = {
        "error_rate_max": rate_max,
        "min_errors_to_alert": min_errors,
        "min_events": min_events,
    }

    if trip and raise_alerts:
        alert = _raise_alert(
            session,
            alert_type="ingestion_errors",
            severity="critical" if error_rate >= rate_max * 2 else "warning",
            title="Ingestion error rate elevated",
            message=(
                f"{n_errors} errors vs {n_ok} successful invoices in the last "
                f"{hours}h (rate {error_rate:.1%} > {rate_max:.0%})."
            ),
            detail=result,
        )
        result["alert_raised"] = alert is not None

    return result


def check_mape_drift(
    session,
    store_id: str,
    *,
    raise_alerts: bool = True,
) -> dict[str, Any]:
    """
    Compare current forecast MAPE / confidence mix vs thresholds and prior
    report snapshot. Alerts on absolute MAPE, drift, or confidence collapse.
    """
    from db.learning import build_mape_summary

    cfg = load_monitoring_config().get("mape", {})
    current = build_mape_summary(session, store_id)
    result: dict[str, Any] = {
        "store_id": store_id,
        "current": current,
        "previous": None,
        "alerts": [],
        "healthy": True,
    }

    if not current.get("available"):
        result["reason"] = current.get("reason", "no forecast data")
        return result

    avg = current.get("avg_mape_pct")
    n_with = int(current.get("n_with_mape") or 0)
    by_conf = current.get("by_confidence") or {}
    n_rows = int(current.get("n_rows") or 0)
    low_share = (by_conf.get("low", 0) / n_rows) if n_rows else 0.0

    avg_max = float(cfg.get("avg_max_pct", 40))
    drift_pts = float(cfg.get("drift_pct_points", 15))
    low_max = float(cfg.get("low_confidence_share_max", 0.6))
    min_mape_rows = int(cfg.get("min_rows_with_mape", 3))

    # Prior report snapshot MAPE (second-most-recent with a usable summary)
    snaps = (
        session.query(ReportSnapshot)
        .filter_by(store_id=store_id)
        .order_by(ReportSnapshot.generated_at.desc())
        .limit(10)
        .all()
    )
    prev_mape = None
    mape_snaps = [
        s
        for s in snaps
        if (s.mape_summary_json or {}).get("available")
        and (s.mape_summary_json or {}).get("avg_mape_pct") is not None
    ]
    if len(mape_snaps) >= 2:
        prev = mape_snaps[1]
        prev_mape = float(prev.mape_summary_json["avg_mape_pct"])
        result["previous"] = {
            "snapshot_id": prev.snapshot_id,
            "avg_mape_pct": prev_mape,
            "generated_at": prev.generated_at.isoformat() + "Z"
            if prev.generated_at
            else None,
        }

    fired: list[str] = []

    if n_with >= min_mape_rows and avg is not None and avg > avg_max:
        fired.append("mape_high")
        if raise_alerts:
            alert = _raise_alert(
                session,
                store_id=store_id,
                alert_type="mape_high",
                severity="warning",
                title="Forecast MAPE above threshold",
                message=f"Average backtest MAPE is {avg}% (threshold {avg_max}%).",
                detail={"avg_mape_pct": avg, "threshold": avg_max, "current": current},
            )
            if alert:
                result["alerts"].append(alert.alert_id)

    if (
        n_with >= min_mape_rows
        and avg is not None
        and prev_mape is not None
        and (avg - prev_mape) >= drift_pts
    ):
        fired.append("mape_drift")
        if raise_alerts:
            alert = _raise_alert(
                session,
                store_id=store_id,
                alert_type="mape_drift",
                severity="warning",
                title="Forecast MAPE drifted worse",
                message=(
                    f"Avg MAPE rose from {prev_mape}% to {avg}% "
                    f"(+{round(avg - prev_mape, 1)} pts; threshold {drift_pts})."
                ),
                detail={
                    "avg_mape_pct": avg,
                    "previous_avg_mape_pct": prev_mape,
                    "drift_pct_points": round(avg - prev_mape, 2),
                },
            )
            if alert:
                result["alerts"].append(alert.alert_id)

    if n_rows >= min_mape_rows and low_share > low_max:
        fired.append("confidence_collapse")
        if raise_alerts:
            alert = _raise_alert(
                session,
                store_id=store_id,
                alert_type="confidence_collapse",
                severity="critical",
                title="Forecast confidence collapsed",
                message=(
                    f"{low_share:.0%} of forecast rows are low confidence "
                    f"(threshold {low_max:.0%})."
                ),
                detail={
                    "low_confidence_share": round(low_share, 4),
                    "by_confidence": by_conf,
                    "threshold": low_max,
                },
            )
            if alert:
                result["alerts"].append(alert.alert_id)

    result["fired"] = fired
    result["healthy"] = len(fired) == 0
    result["low_confidence_share"] = round(low_share, 4)
    result["thresholds"] = {
        "avg_max_pct": avg_max,
        "drift_pct_points": drift_pts,
        "low_confidence_share_max": low_max,
        "min_rows_with_mape": min_mape_rows,
    }
    return result


def run_health_checks(
    session,
    *,
    store_ids: Optional[list[str]] = None,
    raise_alerts: bool = True,
) -> dict[str, Any]:
    """
    Run platform ingestion check + MAPE checks for active (or listed) stores.
    """
    ingestion = check_ingestion_health(session, raise_alerts=raise_alerts)

    if store_ids is None:
        store_ids = [
            s.store_id
            for s in session.query(Subscription).filter_by(status="active").all()
        ]

    mape_results = []
    for sid in store_ids:
        mape_results.append(
            check_mape_drift(session, sid, raise_alerts=raise_alerts)
        )

    open_alerts = (
        session.query(MonitoringAlert)
        .filter_by(status="open")
        .order_by(MonitoringAlert.created_at.desc())
        .all()
    )

    return {
        "ingestion": ingestion,
        "mape": mape_results,
        "open_alert_count": len(open_alerts),
        "healthy": ingestion.get("healthy", True)
        and all(r.get("healthy", True) for r in mape_results),
    }


def list_alerts(
    session,
    *,
    store_id: Optional[str] = None,
    status: str = "open",
    limit: int = 50,
) -> list[MonitoringAlert]:
    q = session.query(MonitoringAlert)
    if status:
        q = q.filter(MonitoringAlert.status == status)
    if store_id is not None:
        q = q.filter(
            (MonitoringAlert.store_id == store_id)
            | (MonitoringAlert.store_id.is_(None))
        )
    return q.order_by(MonitoringAlert.created_at.desc()).limit(limit).all()


def acknowledge_alert(session, alert_id: str) -> Optional[MonitoringAlert]:
    row = session.query(MonitoringAlert).filter_by(alert_id=alert_id).first()
    if not row:
        return None
    row.status = "acknowledged"
    row.acknowledged_at = datetime.utcnow()
    session.flush()
    return row


def alert_to_dict(alert: MonitoringAlert) -> dict[str, Any]:
    return {
        "alert_id": alert.alert_id,
        "store_id": alert.store_id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "message": alert.message,
        "detail": alert.detail_json,
        "status": alert.status,
        "created_at": alert.created_at.isoformat() + "Z" if alert.created_at else None,
        "acknowledged_at": (
            alert.acknowledged_at.isoformat() + "Z" if alert.acknowledged_at else None
        ),
        "resolved_at": (
            alert.resolved_at.isoformat() + "Z" if alert.resolved_at else None
        ),
    }
