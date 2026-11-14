"""SQLAlchemy engine/session factories.

Production uses PostgreSQL (`postgresql+psycopg://...`). Unit tests use
SQLite in-memory. The DSN always comes from environment configuration.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(dsn: str) -> Engine:
    return create_engine(dsn, pool_pre_ping=True, future=True)


def build_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def db_is_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:  # any connection failure is a negative health result
        return False
