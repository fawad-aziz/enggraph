from __future__ import annotations

from enggraph.config import Settings, validate_runtime


def test_demo_mode_ok_when_secret_present():
    s = Settings(
        mode="demo",
        postgres_dsn="sqlite://",
        neo4j_uri="bolt://h:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
        session_secret="x" * 32,
    )
    assert validate_runtime(s) == []


def test_live_mode_requires_credentials():
    s = Settings(
        mode="live",
        postgres_dsn="postgresql+psycopg://u:CHANGE_ME@h:5432/db",
        neo4j_uri="bolt://h:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
        session_secret="",
        azure_devops_pat="",
        azure_devops_organization="",
    )
    problems = validate_runtime(s)
    assert any("ENGGRAPH_SESSION_SECRET" in p for p in problems)
    assert any("POSTGRES_DSN" in p for p in problems)
    assert any("AZURE_DEVOPS_PAT" in p for p in problems)
    assert any("AZURE_DEVOPS_ORGANIZATION" in p for p in problems)


def test_live_mode_with_credentials_passes():
    s = Settings(
        mode="live",
        postgres_dsn="postgresql+psycopg://u:realpass@h:5432/db",
        neo4j_uri="bolt://h:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
        session_secret="x" * 32,
        azure_devops_pat="a-pat",
        azure_devops_organization="myorg",
    )
    assert validate_runtime(s) == []
