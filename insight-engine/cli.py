"""
CLI for the Step 6 Insight Engine (+ Step 10 pilot).

Usage:
    python cli.py run --store-name "Corner Kirana"
    python cli.py run --store-id <uuid> --period-start 2026-07-01 --period-end 2026-07-31
    python cli.py backtest --store-id <uuid>
    python cli.py tune --store-id <uuid> --rule bundle --param min_lift --values 1.1,1.2,1.3
    python cli.py pilot-outcomes --store-id <uuid>
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import typer

app = typer.Typer(help="Deterministic insight engine (no LLM)")


@app.command()
def run(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
    period_start: Optional[str] = typer.Option(None, help="YYYY-MM-DD"),
    period_end: Optional[str] = typer.Option(None, help="YYYY-MM-DD"),
    dry_run: bool = typer.Option(False, help="Score without writing insights table"),
):
    """Build ranked actions from ML result tables for one store."""
    if not store_id and not store_name:
        raise typer.BadParameter("Pass --store-id or --store-name")

    from engine import run_insight_engine

    ps = date.fromisoformat(period_start) if period_start else None
    pe = date.fromisoformat(period_end) if period_end else None
    result = run_insight_engine(
        store_id=store_id,
        store_name=store_name,
        period_start=ps,
        period_end=pe,
        persist=not dry_run,
    )
    typer.echo(
        f"store={result['store_id']} candidates={result['candidates']} "
        f"kept={result['after_conflicts']} persisted={result['persisted']}"
    )
    for i, a in enumerate(result["actions"][:15], 1):
        typer.echo(
            f"  {i}. [{a['priority']}] {a['title']}  "
            f"score={a['score']} impact=₹{a['impact_estimate']}"
        )


@app.command()
def backtest(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
):
    """Step 10: correlate historical accepted actions with next-month revenue lift."""
    if not store_id and not store_name:
        raise typer.BadParameter("Pass --store-id or --store-name")

    from backtest import backtest_historical_months

    result = backtest_historical_months(store_id=store_id, store_name=store_name)
    corr = result["correlation"]
    typer.echo(f"store={result['store_id']} months={corr['n_months_with_next']}")
    typer.echo(
        f"  avg next-month % when acted={corr.get('avg_next_month_pct_when_acted')} "
        f"when idle={corr.get('avg_next_month_pct_when_idle')} "
        f"gap_pp={corr.get('lift_gap_pp')}"
    )
    for s in result["verdict"]["signals"]:
        typer.echo(f"  · {s}")


@app.command("pilot-outcomes")
def pilot_outcomes(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
    baseline_days: int = typer.Option(30),
    followup_days: int = typer.Option(30),
):
    """Step 10: before/after revenue for accepted actions."""
    if not store_id and not store_name:
        raise typer.BadParameter("Pass --store-id or --store-name")

    from db.session import get_session, init_db
    from db.models import Store
    from db.pilot import accepted_action_outcomes, summarize_outcomes

    init_db()
    session = get_session()
    try:
        sid = store_id
        if not sid and store_name:
            store = session.query(Store).filter(Store.store_name == store_name).first()
            if not store:
                raise typer.BadParameter(f"Store not found: {store_name}")
            sid = store.store_id
        rows = accepted_action_outcomes(
            session, sid, baseline_days=baseline_days, followup_days=followup_days
        )
        summary = summarize_outcomes(rows)
        typer.echo(
            f"store={sid} acted={summary['n_acted']} measurable={summary['n_measurable']} "
            f"avg_pct={summary.get('avg_revenue_pct_change')}"
        )
        for o in rows[:15]:
            pct = o.get("revenue_pct_change")
            typer.echo(
                f"  [{o['action_type']}] {o['title'][:50]}  "
                f"Δ%={pct if pct is not None else 'n/a'}  "
                f"resolved={o['resolved_at']}"
            )
    finally:
        session.close()


@app.command()
def tune(
    store_id: Optional[str] = typer.Option(None),
    store_name: Optional[str] = typer.Option(None),
    rule: str = typer.Option(..., help="Rule name, e.g. bundle"),
    param: str = typer.Option(..., help="Parameter under rules.<rule>, e.g. min_lift"),
    values: str = typer.Option(..., help="Comma-separated values, e.g. 1.1,1.2,1.3"),
):
    """Step 10: simulate threshold grid against current ML snapshot."""
    if not store_id and not store_name:
        raise typer.BadParameter("Pass --store-id or --store-name")

    from tuning import simulate_rule_thresholds

    vals = [v.strip() for v in values.split(",") if v.strip()]
    result = simulate_rule_thresholds(
        store_id=store_id,
        store_name=store_name,
        rule_name=rule,
        param=param,
        values=vals,
    )
    typer.echo(f"store={result['store_id']} rule={rule}.{param}")
    for row in result["grid"]:
        typer.echo(
            f"  {param}={row['value']}: n={row['n_candidates']} "
            f"avg_score={row['avg_score']} "
            f"useful_overlap={row['overlap_useful']} "
            f"not_useful_overlap={row['overlap_not_useful']}"
        )
    typer.echo(result["hint"])


if __name__ == "__main__":
    app()
