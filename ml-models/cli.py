"""
CLI entrypoint for all ML jobs.

Usage:
    python cli.py run-forecast [--horizon 30] [--store-id ID | --store-name NAME]
    python cli.py run-basket-analysis
    python cli.py run-abc-classification
    python cli.py run-elasticity
    python cli.py run-anomaly-detection
    python cli.py run-customer-targeting
    python cli.py run-inventory
    python cli.py run-timing
    python cli.py run-benchmarking
    python cli.py run-festival
    python cli.py run-all [--store-id ID | --store-name NAME]
    python cli.py backtest-forecast --cutoff-date 2026-06-01 [--horizon 14]
    python cli.py nightly-refresh [--force] [--skip-forecast]
    python cli.py health-check
"""
from typing import Optional

import typer

app = typer.Typer()


def _resolve(store_id: Optional[str], store_name: Optional[str]) -> Optional[str]:
    if not store_id and not store_name:
        return None
    from common.data_loaders import get_session, resolve_store_id
    session = get_session()
    try:
        return resolve_store_id(session, store_id=store_id, store_name=store_name)
    finally:
        session.close()


@app.command()
def run_forecast(
    horizon: int = typer.Option(None, help="Forecast horizon in days (default from config)"),
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from forecasting.job import run_forecast_job
    sid = _resolve(store_id, store_name)
    result = run_forecast_job(horizon_days=horizon, store_id=sid)
    typer.echo(result)


@app.command()
def run_basket_analysis(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from basket_analysis.job import run_basket_analysis_job
    typer.echo(run_basket_analysis_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_abc_classification(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from abc_classification.job import run_abc_classification_job
    typer.echo(run_abc_classification_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_elasticity(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from elasticity.job import run_elasticity_job
    typer.echo(run_elasticity_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_anomaly_detection(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from anomaly_detection.job import run_anomaly_detection_job
    typer.echo(run_anomaly_detection_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_customer_targeting(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from customer_targeting.job import run_customer_targeting_job
    typer.echo(run_customer_targeting_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_inventory(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from inventory.job import run_inventory_job
    typer.echo(run_inventory_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_timing(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from timing.job import run_timing_job
    typer.echo(run_timing_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_benchmarking(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from store_benchmarking.job import run_benchmarking_job
    typer.echo(run_benchmarking_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_festival(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    from festival.job import run_festival_job
    typer.echo(run_festival_job(store_id=_resolve(store_id, store_name)))


@app.command()
def run_all(
    store_id: Optional[str] = typer.Option(None, help="Scope all jobs to this store_id"),
    store_name: Optional[str] = typer.Option(None, help="Scope all jobs to this store name"),
    skip_forecast: bool = typer.Option(False, help="Skip Prophet (faster for smoke tests)"),
    horizon: int = typer.Option(None, help="Forecast horizon days"),
):
    """Run all ML modules in sequence (forecasting last)."""
    from orchestrator import run_all_modules

    results = run_all_modules(
        store_id=store_id,
        store_name=store_name,
        horizon_days=horizon,
        skip_forecast=skip_forecast,
    )
    for name, payload in results.items():
        if name == "store_id":
            continue
        typer.echo(f"=== {name} ===")
        typer.echo(payload)


@app.command()
def backtest_forecast(cutoff_date: str = typer.Option(..., help="YYYY-MM-DD"), horizon: int = 14):
    from forecasting.backtest import backtest
    df = backtest(cutoff_date, horizon_days=horizon)
    typer.echo(df.to_string(index=False))
    valid = df["mape_pct"].dropna()
    if not valid.empty:
        typer.echo(f"\nAverage MAPE across products: {valid.mean():.2f}%")


@app.command("nightly-refresh")
def nightly_refresh(
    force: bool = typer.Option(False, help="Refresh all active stores, ignoring ml_refresh_hours"),
    skip_forecast: bool = typer.Option(False, help="Skip Prophet (faster smoke)"),
    skip_health: bool = typer.Option(False, help="Skip ingestion/MAPE health checks after refresh"),
):
    """
    Step 9 — Refresh ML for active subscriptions that are due, then health-check.
    Cron-friendly: 0 2 * * * cd ml-models && python cli.py nightly-refresh
    """
    from nightly import run_nightly_refresh

    result = run_nightly_refresh(
        force=force,
        triggered_by="cli",
        skip_forecast=skip_forecast,
        run_health=not skip_health,
    )
    typer.echo(result)


@app.command("health-check")
def health_check_cmd():
    """Step 9 — Ingestion error rates + forecast MAPE / confidence alerts."""
    from nightly import run_health_check_job

    result = run_health_check_job(triggered_by="cli")
    typer.echo(
        {
            "job_id": result.get("job_id"),
            "healthy": result.get("healthy"),
            "open_alert_count": result.get("open_alert_count"),
            "ingestion": result.get("ingestion"),
            "mape_fired": [
                {"store_id": r["store_id"], "fired": r.get("fired")}
                for r in result.get("mape", [])
                if r.get("fired")
            ],
        }
    )


if __name__ == "__main__":
    app()
