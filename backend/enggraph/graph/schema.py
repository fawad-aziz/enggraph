"""Graph schema: uniqueness constraints and indexes (Neo4j 5.x syntax).

Statements use IF NOT EXISTS so installation is repeatable. Executed at
startup via scripts/docker_entrypoint.sh (python -m enggraph.install_schema).
"""

from __future__ import annotations

from neo4j import Driver

CONSTRAINT_LABELS = [
    "Organization",
    "Project",
    "Team",
    "Developer",
    "Repository",
    "WorkItem",
    "PullRequest",
    "Commit",
    "Service",
    "API",
]

SCHEMA_STATEMENTS: list[str] = [
    # Uniqueness constraints on canonical id (graph model doc §6)
    *[
        f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in CONSTRAINT_LABELS
    ],
    # Indexes for frequently searched fields
    "CREATE INDEX project_name IF NOT EXISTS FOR (n:Project) ON (n.name)",
    "CREATE INDEX team_name IF NOT EXISTS FOR (n:Team) ON (n.name)",
    "CREATE INDEX developer_name IF NOT EXISTS FOR (n:Developer) ON (n.displayName)",
    "CREATE INDEX repository_name IF NOT EXISTS FOR (n:Repository) ON (n.name)",
    "CREATE INDEX workitem_title IF NOT EXISTS FOR (n:WorkItem) ON (n.title)",
    "CREATE INDEX pullrequest_title IF NOT EXISTS FOR (n:PullRequest) ON (n.title)",
    "CREATE INDEX service_name IF NOT EXISTS FOR (n:Service) ON (n.name)",
    "CREATE INDEX api_name IF NOT EXISTS FOR (n:API) ON (n.name)",
    # Organization scoping indexes (every query filters on organizationId)
    *[
        f"CREATE INDEX {label.lower()}_org_scope IF NOT EXISTS "
        f"FOR (n:{label}) ON (n.organizationId)"
        for label in CONSTRAINT_LABELS
    ],
]


def install_schema(driver: Driver, database: str = "neo4j") -> None:
    """Install constraints and indexes; safe to run repeatedly."""
    for statement in SCHEMA_STATEMENTS:
        driver.execute_query(statement, database_=database)
