"""
Database engine/session setup.

SQLite is the default for local demos. For multi-tenant production,
set DB_URL to PostgreSQL, e.g.:
    postgresql+psycopg2://user:password@host:5432/invoice_db
Everything else (models, queries) works unchanged since we only use
standard SQLAlchemy column types.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base

DB_URL = os.environ.get("INVOICE_DB_URL", "sqlite:///./invoice_pipeline.db")

# check_same_thread only needed for SQLite
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create all tables. In production, use Alembic migrations instead."""
    Base.metadata.create_all(bind=engine)
    # Seed plan catalog so new subscriptions can resolve quotas immediately.
    session = SessionLocal()
    try:
        from db.plans import seed_subscription_plans

        seed_subscription_plans(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session():
    return SessionLocal()
