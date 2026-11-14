"""M4 schema tests: the Alembic migration applies and matches the models.

Run against SQLite in a temp file (the schema is portable; production runs it
against Postgres). Checks the security-relevant shape: no PAT column, hashes
only, checkpoint/job uniqueness, index presence.
"""

from __future__ import annotations

import pathlib

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from enggraph.persistence.models import Base

BACKEND = pathlib.Path(__file__).parent.parent
INI = BACKEND / "alembic.ini"


def make_config(dsn: str) -> Config:
    cfg = Config(str(INI))
    cfg.set_main_option("script_location", (BACKEND / "alembic").as_posix())
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


@pytest.fixture()
def dsn(tmp_path, monkeypatch) -> str:
    monkeypatch.delenv("ENGGRAPH_POSTGRES_DSN", raising=False)
    db = tmp_path / "m4.db"
    return f"sqlite:///{db.as_posix()}"


def test_upgrade_creates_every_model_table(dsn):
    command.upgrade(make_config(dsn), "head")
    inspector = sa.inspect(sa.create_engine(dsn))
    tables = set(inspector.get_table_names())
    assert tables == set(Base.metadata.tables) | {"alembic_version"}


def test_migration_columns_match_models(dsn):
    command.upgrade(make_config(dsn), "head")
    inspector = sa.inspect(sa.create_engine(dsn))
    for name, table in Base.metadata.tables.items():
        model_columns = {c.name for c in table.columns}
        db_columns = {c["name"] for c in inspector.get_columns(name)}
        assert db_columns == model_columns, f"column drift in {name}"


def test_connector_configs_stores_a_secret_reference_not_a_pat(dsn):
    command.upgrade(make_config(dsn), "head")
    inspector = sa.inspect(sa.create_engine(dsn))
    columns = {c["name"] for c in inspector.get_columns("connector_configs")}
    assert "pat_secret_ref" in columns
    # No column that could hold a raw PAT / secret value.
    assert not any(c == "pat" or c.endswith("_pat") or "token" in c for c in columns)


def test_token_hash_index_is_unique(dsn):
    command.upgrade(make_config(dsn), "head")
    inspector = sa.inspect(sa.create_engine(dsn))
    unique = {i["name"] for i in inspector.get_indexes("sessions") if i.get("unique")}
    assert "ix_sessions_token_hash" in unique


def test_checkpoint_scope_is_unique_per_connector(dsn):
    command.upgrade(make_config(dsn), "head")
    inspector = sa.inspect(sa.create_engine(dsn))
    constraints = {
        tuple(c["column_names"])
        for c in inspector.get_unique_constraints("sync_checkpoints")
    }
    assert ("organization_id", "connector_config_id", "scope") in constraints


def test_version_is_stamped_and_downgrade_removes_tables(dsn):
    cfg = make_config(dsn)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(dsn)
    with engine.connect() as conn:
        version = conn.execute(sa.text("select version_num from alembic_version")).scalar()
    assert version == "0001"
    command.downgrade(cfg, "base")
    tables = set(sa.inspect(sa.create_engine(dsn)).get_table_names())
    assert tables == {"alembic_version"}


def test_env_variable_dsn_wins_over_the_placeholder(tmp_path, monkeypatch):
    # Placeholder credentials in alembic.ini must never be used when the
    # environment supplies a real DSN: the migration lands in the env database.
    real = f"sqlite:///{(tmp_path / 'env.db').as_posix()}"
    monkeypatch.setenv("ENGGRAPH_POSTGRES_DSN", real)
    command.upgrade(make_config("sqlite:///:memory:"), "head")
    tables = set(sa.inspect(sa.create_engine(real)).get_table_names())
    assert "sync_jobs" in tables
