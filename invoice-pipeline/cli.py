"""
CLI entrypoint for the invoice ingestion pipeline.

Usage:
    python cli.py init-db
    python cli.py ingest --file path/to/invoice.csv --type csv
    python cli.py batch-ingest --folder path/to/invoices --type csv
    python cli.py issue-api-key --store-name "My Store"
    python cli.py set-password --store-name "My Store" --username owner --password secret1
    python cli.py set-plan --store-name "My Store" --plan pro
    python cli.py rollup-revenue
    python cli.py backfill-loyalty
    python cli.py loyalty-lookup --phone 9876543210 --store-name "My Store"
    python cli.py show-errors
    python cli.py seed-festivals
"""
from datetime import date as date_cls
from typing import Optional

import typer
from db.session import init_db, get_session
from db.models import IngestionError, Invoice, Store, Product, FestivalEvent, Customer, Subscription
from db.seed_festivals import seed_festival_events
from db.api_keys import issue_api_key
from db.store_auth import set_store_password
from db.plans import set_store_plan, seed_subscription_plans
from db.revenue_facts import rollup_revenue_facts
from db.loyalty import backfill_loyalty, lookup_by_phone, list_rewards
from pipeline.ingest import ingest_file, batch_ingest

app = typer.Typer()


@app.command()
def init_db_cmd():
    """Create all database tables and seed festival calendar + plans."""
    init_db()
    session = get_session()
    try:
        n_plans = seed_subscription_plans(session)
        session.commit()
    finally:
        session.close()
    n = seed_festival_events()
    typer.echo(f"Database initialized. Festival rows inserted: {n}. Plans upserted: {n_plans}.")


@app.command()
def seed_festivals():
    """Insert Indian festival calendar rows (idempotent)."""
    init_db()
    n = seed_festival_events()
    typer.echo(f"Festival rows inserted: {n}")


@app.command("issue-api-key")
def issue_api_key_cmd(
    store_name: str = typer.Option(None, "--store-name", help="Store name (created if missing)"),
    store_id: str = typer.Option(None, "--store-id", help="Existing store UUID"),
    plan: str = typer.Option("basic", "--plan", help="free | basic | pro"),
):
    """Issue (or rotate) a live-ingest API key for a store. Printed once."""
    if not store_name and not store_id:
        typer.echo("Provide --store-name or --store-id", err=True)
        raise typer.Exit(code=1)
    init_db()
    session = get_session()
    try:
        issued = issue_api_key(session, store_id=store_id, store_name=store_name, plan=plan)
        session.commit()
        typer.echo(f"store_id={issued.store_id}")
        typer.echo(f"plan={issued.plan}")
        typer.echo(f"api_key_prefix={issued.prefix}")
        typer.echo(f"api_key={issued.api_key}")
        typer.echo("Save this api_key now — it cannot be retrieved again.")
    except Exception as e:
        session.rollback()
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@app.command("set-password")
def set_password_cmd(
    username: str = typer.Option(..., "--username", help="Dashboard login username"),
    password: str = typer.Option(..., "--password", help="Dashboard login password"),
    store_name: str = typer.Option(None, "--store-name", help="Store name (created if missing)"),
    store_id: str = typer.Option(None, "--store-id", help="Existing store UUID"),
):
    """Create or update a store dashboard login."""
    if not store_name and not store_id:
        typer.echo("Provide --store-name or --store-id", err=True)
        raise typer.Exit(code=1)
    init_db()
    session = get_session()
    try:
        user = set_store_password(
            session,
            store_id=store_id,
            store_name=store_name,
            username=username,
            password=password,
        )
        session.commit()
        typer.echo(f"store_id={user.store_id}")
        typer.echo(f"username={user.username}")
        typer.echo("Password set. Use POST /api/v1/auth/login to get a session token.")
    except Exception as e:
        session.rollback()
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@app.command("set-plan")
def set_plan_cmd(
    plan: str = typer.Option(..., "--plan", help="free | basic | pro"),
    store_name: str = typer.Option(None, "--store-name"),
    store_id: str = typer.Option(None, "--store-id"),
):
    """Change a store's subscription plan and refresh quotas from the catalog."""
    if not store_name and not store_id:
        typer.echo("Provide --store-name or --store-id", err=True)
        raise typer.Exit(code=1)
    init_db()
    session = get_session()
    try:
        store = None
        if store_id:
            store = session.query(Store).filter_by(store_id=store_id).first()
        elif store_name:
            store = session.query(Store).filter_by(store_name=store_name).first()
        if not store:
            typer.echo("Store not found", err=True)
            raise typer.Exit(code=1)
        sub = session.query(Subscription).filter_by(store_id=store.store_id).first()
        if not sub:
            issued = issue_api_key(session, store_id=store.store_id, plan=plan)
            session.commit()
            typer.echo(f"store_id={issued.store_id}")
            typer.echo(f"plan={issued.plan}")
            return
        sub = set_store_plan(session, store.store_id, plan)
        session.commit()
        typer.echo(f"store_id={store.store_id}")
        typer.echo(f"plan={sub.plan}")
        typer.echo(f"monthly_report_quota={sub.monthly_report_quota}")
        typer.echo(f"require_customer_phone={sub.require_customer_phone}")
    except Exception as e:
        session.rollback()
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@app.command("rollup-revenue")
def rollup_revenue_cmd(
    store_name: Optional[str] = typer.Option(None, "--store-name"),
    store_id: Optional[str] = typer.Option(None, "--store-id"),
    start_date: Optional[str] = typer.Option(None, "--start-date", help="YYYY-MM-DD"),
    end_date: Optional[str] = typer.Option(None, "--end-date", help="YYYY-MM-DD"),
):
    """Recompute daily + monthly revenue facts from invoices (Step 3)."""
    init_db()
    session = get_session()
    try:
        sid = store_id
        if store_name and not sid:
            store = session.query(Store).filter_by(store_name=store_name).first()
            if not store:
                typer.echo("Store not found", err=True)
                raise typer.Exit(code=1)
            sid = store.store_id
        start = date_cls.fromisoformat(start_date) if start_date else None
        end = date_cls.fromisoformat(end_date) if end_date else None
        result = rollup_revenue_facts(
            session, store_id=sid, start_date=start, end_date=end
        )
        session.commit()
        typer.echo(
            f"Daily facts upserted: {result['daily_rows']} | "
            f"Monthly facts upserted: {result['monthly_rows']}"
        )
    except Exception as e:
        session.rollback()
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@app.command("backfill-loyalty")
def backfill_loyalty_cmd(
    store_name: Optional[str] = typer.Option(None, "--store-name"),
    store_id: Optional[str] = typer.Option(None, "--store-id"),
):
    """Accrue loyalty points for historical invoices (Pro / loyalty plans)."""
    init_db()
    session = get_session()
    try:
        sid = store_id
        if store_name and not sid:
            store = session.query(Store).filter_by(store_name=store_name).first()
            if not store:
                typer.echo("Store not found", err=True)
                raise typer.Exit(code=1)
            sid = store.store_id
        result = backfill_loyalty(session, store_id=sid)
        session.commit()
        typer.echo(
            f"Earned: {result['earned']} | Skipped: {result['skipped']} | "
            f"Already earned: {result['duplicate']}"
        )
    except Exception as e:
        session.rollback()
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@app.command("loyalty-lookup")
def loyalty_lookup_cmd(
    phone: str = typer.Option(..., "--phone"),
    store_name: Optional[str] = typer.Option(None, "--store-name"),
    store_id: Optional[str] = typer.Option(None, "--store-id"),
):
    """Look up loyalty balance / tier by customer phone for a store."""
    if not store_name and not store_id:
        typer.echo("Provide --store-name or --store-id", err=True)
        raise typer.Exit(code=1)
    init_db()
    session = get_session()
    try:
        sid = store_id
        if store_name and not sid:
            store = session.query(Store).filter_by(store_name=store_name).first()
            if not store:
                typer.echo("Store not found", err=True)
                raise typer.Exit(code=1)
            sid = store.store_id
        result = lookup_by_phone(session, sid, phone)
        session.commit()
        if not result.found and not result.customer_id:
            typer.echo(f"Not found: {result.detail}")
            raise typer.Exit(code=1)
        typer.echo(f"phone={result.phone_normalized}")
        typer.echo(f"customer_id={result.customer_id}")
        typer.echo(f"name={result.customer_name}")
        typer.echo(f"points={result.points_balance}")
        typer.echo(f"tier={result.tier}")
        typer.echo(f"lifetime_spend={result.lifetime_spend}")
        typer.echo(f"visits={result.visits}")
        typer.echo("Rewards:")
        for r in list_rewards():
            typer.echo(f"  {r['code']}: {r['title']} ({r['points_cost']} pts)")
    except typer.Exit:
        raise
    except Exception as e:
        session.rollback()
        typer.echo(f"Failed: {e}", err=True)
        raise typer.Exit(code=1)
    finally:
        session.close()


@app.command()
def ingest(
    file: str = typer.Option(..., "--file", help="Path to the invoice file"),
    type: str = typer.Option(..., "--type", help="csv | json | pdf | ocr_image"),
    mapping_profile: str = typer.Option("default", "--mapping-profile"),
):
    """Ingest a single file."""
    init_db()
    result = ingest_file(file, type, mapping_profile)
    typer.echo(f"Found: {result.invoices_found} | Loaded: {result.invoices_loaded} | "
               f"Duplicates: {result.invoices_skipped_duplicate} | "
               f"Failed validation: {result.invoices_failed_validation}")
    if result.errors:
        typer.echo(f"Errors: {result.errors}")


@app.command()
def batch_ingest_cmd(
    folder: str = typer.Option(..., "--folder", help="Folder of invoice files"),
    type: str = typer.Option(..., "--type", help="csv | json | pdf | ocr_image"),
    mapping_profile: str = typer.Option("default", "--mapping-profile"),
):
    """Ingest every file in a folder."""
    init_db()
    result = batch_ingest(folder, type, mapping_profile)
    typer.echo(result.summary())


@app.command()
def show_errors(limit: int = 20):
    """Show the most recent ingestion errors."""
    session = get_session()
    try:
        errors = session.query(IngestionError).order_by(IngestionError.error_id.desc()).limit(limit).all()
        if not errors:
            typer.echo("No ingestion errors logged.")
        for e in errors:
            typer.echo(f"[{e.error_type}] {e.source_file_path}: {e.error_detail}")
    finally:
        session.close()


@app.command()
def summary():
    """Show a quick summary of what's in the database."""
    session = get_session()
    try:
        n_invoices = session.query(Invoice).count()
        n_stores = session.query(Store).count()
        n_products = session.query(Product).count()
        n_errors = session.query(IngestionError).count()
        n_customers = session.query(Customer).count()
        n_festivals = session.query(FestivalEvent).count()
        typer.echo(
            f"Stores: {n_stores} | Products: {n_products} | "
            f"Invoices: {n_invoices} | Customers: {n_customers} | "
            f"Festivals: {n_festivals} | Logged errors: {n_errors}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    app()
