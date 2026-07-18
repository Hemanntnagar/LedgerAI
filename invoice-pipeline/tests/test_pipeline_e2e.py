"""End-to-end pipeline tests. Uses a temporary SQLite DB (isolated from
the dev DB used for manual CLI testing) so tests don't interfere with
each other or with real data."""
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def isolated_db(monkeypatch):
    """Point the pipeline at a fresh temp SQLite file for this test only."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")
    monkeypatch.setenv("INVOICE_DB_URL", f"sqlite:///{db_path}")

    # db.session reads the env var at import time, so re-import fresh
    import importlib
    import db.session as session_module
    importlib.reload(session_module)
    session_module.init_db()

    yield session_module

    session_module.engine.dispose()


def test_full_ingest_csv_end_to_end(isolated_db, monkeypatch):
    import importlib
    import pipeline.ingest as ingest_module
    importlib.reload(ingest_module)

    result = ingest_module.ingest_file(
        os.path.join(FIXTURES, "sample_invoices.csv"), source_type="csv"
    )

    # Fixture has 7 distinct invoice numbers (INV-1001..INV-1007):
    # 5 are valid, 1 has a negative quantity, 1 has a missing product name
    # (which empties out its only line item -> "no_line_items").
    assert result.invoices_found == 7
    assert result.invoices_loaded == 5
    assert result.invoices_failed_validation == 2
    assert result.invoices_skipped_duplicate == 0


def test_rerunning_same_file_is_idempotent(isolated_db, monkeypatch):
    import importlib
    import pipeline.ingest as ingest_module
    importlib.reload(ingest_module)

    file_path = os.path.join(FIXTURES, "sample_invoices.csv")
    first = ingest_module.ingest_file(file_path, source_type="csv")
    second = ingest_module.ingest_file(file_path, source_type="csv")

    assert first.invoices_loaded == 5
    # Second run should find the same invoices as duplicates, not reload them
    assert second.invoices_loaded == 0
    assert second.invoices_skipped_duplicate == 5


def test_bad_invoice_does_not_block_others_in_same_file(isolated_db, monkeypatch):
    import importlib
    import pipeline.ingest as ingest_module
    importlib.reload(ingest_module)

    result = ingest_module.ingest_file(
        os.path.join(FIXTURES, "sample_invoices.csv"), source_type="csv"
    )
    # The file has a negative-quantity invoice and a no-product-name invoice,
    # but the other 5 valid invoices must still load successfully.
    assert result.invoices_loaded == 5
    assert result.invoices_failed_validation == 2


def test_ingestion_errors_are_logged_for_review(isolated_db, monkeypatch):
    import importlib
    import pipeline.ingest as ingest_module
    importlib.reload(ingest_module)
    from db.models import IngestionError

    ingest_module.ingest_file(
        os.path.join(FIXTURES, "sample_invoices.csv"), source_type="csv"
    )

    session = isolated_db.get_session()
    try:
        errors = session.query(IngestionError).all()
        error_types = {e.error_type for e in errors}
        assert "invoice_validation_failed" in error_types
    finally:
        session.close()
