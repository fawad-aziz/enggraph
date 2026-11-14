"""Alembic environment. DSN comes from ENGGRAPH_POSTGRES_DSN when set."""

from __future__ import annotations

import os

from sqlalchemy import create_engine

from alembic import context
from enggraph.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _dsn() -> str:
    return os.environ.get("ENGGRAPH_POSTGRES_DSN") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_dsn())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
