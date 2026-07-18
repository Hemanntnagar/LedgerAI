"""
CLI entrypoint for the invoice ingestion pipeline.

Usage:
    python cli.py init-db
    python cli.py ingest --file path/to/invoice.csv --type csv
    python cli.py batch-ingest --folder path/to/invoices --type csv
    python cli.py show-errors
"""
import typer
from db.session import init_db, get_session
from db.models import IngestionError, Invoice, Store, Product
from pipeline.ingest import ingest_file, batch_ingest

app = typer.Typer()


@app.command()
def init_db_cmd():
    """Create all database tables."""
    init_db()
    typer.echo("Database initialized.")


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
        typer.echo(f"Stores: {n_stores} | Products: {n_products} | "
                   f"Invoices: {n_invoices} | Logged errors: {n_errors}")
    finally:
        session.close()


if __name__ == "__main__":
    app()
