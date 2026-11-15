"""Alembic environment. DSN comes from the environment when set.

`ENGGRAPH_POSTGRES_DSN` wins if present; otherwise the app's own
`POSTGRES_DSN` (the alias `enggraph.config.Settings` reads) is used, so
`cp .env.example .env` + `cd backend && alembic upgrade head` needs no extra
variable. The `alembic.ini` URL is the last fallback and holds a placeholder,
not a credential — a run that reaches it is a run that was not configured.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine

from alembic import context
from enggraph.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _dsn() -> str:
    return (
        os.environ.get("ENGGRAPH_POSTGRES_DSN")
        or os.environ.get("POSTGRES_DSN")
        or config.get_main_option("sqlalchemy.url")
    )


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
