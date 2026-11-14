"""Schema statement checks (syntax per Neo4j 5.x). Executed against real
Neo4j by test_neo4j_integration.py when a test instance is available."""

from __future__ import annotations

import re

from enggraph.graph.model import NODE_LABELS
from enggraph.graph.schema import SCHEMA_STATEMENTS

CONSTRAINT_RE = re.compile(
    r"^CREATE CONSTRAINT \w+ IF NOT EXISTS FOR \(n:(\w+)\) REQUIRE n\.id IS UNIQUE$"
)


def test_every_label_has_uniqueness_constraint():
    constrained = {m.group(1) for s in SCHEMA_STATEMENTS if (m := CONSTRAINT_RE.match(s))}
    assert constrained == set(NODE_LABELS)


def test_all_statements_are_idempotent_create_if_not_exists():
    for stmt in SCHEMA_STATEMENTS:
        assert stmt.startswith(("CREATE CONSTRAINT ", "CREATE INDEX "))
        assert "IF NOT EXISTS" in stmt


def test_org_scope_index_on_every_label():
    scoped = {
        s.split("FOR (n:")[1].split(")")[0]
        for s in SCHEMA_STATEMENTS
        if "ON (n.organizationId)" in s
    }
    assert scoped == set(NODE_LABELS)
