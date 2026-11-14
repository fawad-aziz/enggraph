"""Shared test fixtures: settings, in-memory DB, fake Neo4j driver."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from enggraph.config import Settings


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        mode="demo",
        postgres_dsn="sqlite://",
        neo4j_uri="bolt://test:7687",
        neo4j_user="neo4j",
        neo4j_password="testpw",
        session_secret="test-secret-32-bytes-long-aaaaaaa",
    )


@pytest.fixture()
def engine() -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    return engine


class FakeDriver:
    """Test double implementing only what health checks exercise.

    Never used by production code — production uses the official neo4j
    driver. This exists so API tests run without a Neo4j container.
    """

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.closed = False

    def verify_connectivity(self) -> None:
        if not self.reachable:
            raise RuntimeError("unreachable")

    def close(self) -> None:
        self.closed = True

    def session(self, **kwargs: Any) -> Any:  # pragma: no cover - not used by M1 tests
        raise NotImplementedError("FakeDriver has no session support")


@pytest.fixture()
def fake_driver() -> FakeDriver:
    return FakeDriver()
