"""Neo4j driver lifecycle.

The driver is a long-lived, thread-safe object shared by the app.
Sessions are created per request/transaction and never shared across
concurrent requests (architecture doc §2.5).
"""

from __future__ import annotations

import logging

from neo4j import Driver, GraphDatabase

log = logging.getLogger("enggraph.graph.driver")


def create_driver(uri: str, user: str, password: str) -> Driver:
    driver = GraphDatabase.driver(
        uri,
        auth=(user, password),
        # Bound the driver's connection pool; never unbounded.
        max_connection_pool_size=50,
        connection_timeout=10,
    )
    return driver


def driver_is_reachable(driver: Driver) -> bool:
    try:
        driver.verify_connectivity()
        return True
    except Exception:
        return False


def open_driver(uri: str, user: str, password: str) -> Driver | None:
    """Create a driver; returns None if Neo4j is unreachable (health
    readiness reports it; app stays up)."""
    try:
        d = create_driver(uri, user, password)
        d.verify_connectivity()
        return d
    except Exception as exc:
        log.warning("neo4j unreachable at startup", extra={"error": str(exc), "uri": uri})
        return None
