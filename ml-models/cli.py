"""
CLI entrypoint for all ML jobs.

Usage:
    python cli.py run-forecast [--horizon 30]
    python cli.py run-basket-analysis
    python cli.py run-abc-classification
    python cli.py run-elasticity
    python cli.py run-anomaly-detection
    python cli.py run-all
    python cli.py backtest-forecast --cutoff-date 2026-06-01 [--horizon 14]
"""
import typer

app = typer.Typer()


@app.command()
def run_forecast(horizon: int = typer.Option(None, help="Forecast horizon in days (default from config)")):
    from forecasting.job import run_forecast_job
    result = run_forecast_job(horizon_days=horizon)
    typer.echo(result)


@app.command()
def run_basket_analysis():
    from basket_analysis.job import run_basket_analysis_job
    result = run_basket_analysis_job()
    typer.echo(result)


@app.command()
def run_abc_classification():
    from abc_classification.job import run_abc_classification_job
    result = run_abc_classification_job()
    typer.echo(result)


@app.command()
def run_elasticity():
    from elasticity.job import run_elasticity_job
    result = run_elasticity_job()
    typer.echo(result)


@app.command()
def run_anomaly_detection():
    from anomaly_detection.job import run_anomaly_detection_job
    result = run_anomaly_detection_job()
    typer.echo(result)


@app.command()
def run_all():
    """Run all 5 modules in sequence (forecasting last since it's slowest)."""
    from basket_analysis.job import run_basket_analysis_job
    from abc_classification.job import run_abc_classification_job
    from elasticity.job import run_elasticity_job
    from anomaly_detection.job import run_anomaly_detection_job
    from forecasting.job import run_forecast_job

    typer.echo("=== ABC Classification ===")
    typer.echo(run_abc_classification_job())
    typer.echo("=== Basket Analysis ===")
    typer.echo(run_basket_analysis_job())
    typer.echo("=== Price Elasticity ===")
    typer.echo(run_elasticity_job())
    typer.echo("=== Anomaly Detection ===")
    typer.echo(run_anomaly_detection_job())
    typer.echo("=== Demand Forecasting ===")
    typer.echo(run_forecast_job())


@app.command()
def backtest_forecast(cutoff_date: str = typer.Option(..., help="YYYY-MM-DD"), horizon: int = 14):
    from forecasting.backtest import backtest
    df = backtest(cutoff_date, horizon_days=horizon)
    typer.echo(df.to_string(index=False))
    valid = df["mape_pct"].dropna()
    if not valid.empty:
        typer.echo(f"\nAverage MAPE across products: {valid.mean():.2f}%")


if __name__ == "__main__":
    app()
