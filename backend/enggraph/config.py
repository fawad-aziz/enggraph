"""Application configuration loaded from environment (never from code)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppMode = Literal["demo", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    mode: AppMode = Field("demo", alias="ENGGRAPH_MODE")
    postgres_dsn: str = Field(
        "postgresql+psycopg://enggraph:CHANGE_ME@localhost:5432/enggraph",
        alias="POSTGRES_DSN",
    )
    neo4j_uri: str = Field("bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field("neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field("", alias="NEO4J_PASSWORD")
    session_secret: str = Field("", alias="ENGGRAPH_SESSION_SECRET")
    azure_devops_pat: str = Field("", alias="AZURE_DEVOPS_PAT")
    azure_devops_organization: str = Field("", alias="AZURE_DEVOPS_ORGANIZATION")
    max_depth: int = Field(4, alias="ENGGRAPH_MAX_DEPTH", ge=1, le=10)
    max_results: int = Field(200, alias="ENGGRAPH_MAX_RESULTS", ge=1, le=1000)
    session_ttl_hours: int = Field(12, alias="ENGGRAPH_SESSION_TTL_HOURS", ge=1, le=72)
    backend_port: int = Field(8000, alias="BACKEND_PORT")
    mcp_host: str = Field("127.0.0.1", alias="MCP_HOST")
    mcp_port: int = Field(8100, alias="MCP_PORT")
    # Extra Host/Origin values a client presents when the server is reached through
    # something that is not its own bind address — a container's published port, a
    # proxy in front. Comma separated; the MCP transport matches `host:*` patterns.
    mcp_allowed_hosts: str = Field("", alias="MCP_ALLOWED_HOSTS")
    mcp_allowed_origins: str = Field("", alias="MCP_ALLOWED_ORIGINS")


def load_settings() -> Settings:
    # Every field has a default, so `Settings()` is valid at runtime: values come
    # from the environment/.env. The mypy pydantic plugin types alias-named fields
    # as required constructor arguments, so the bare call is annotated as allowed.
    return Settings()  # type: ignore[call-arg]


def validate_runtime(settings: Settings) -> list[str]:
    """Return human-readable configuration problems. Empty list = OK."""
    problems: list[str] = []
    if settings.mode == "live":
        if not settings.session_secret:
            problems.append("ENGGRAPH_SESSION_SECRET is required in live mode")
        if "CHANGE_ME" in settings.postgres_dsn:
            problems.append("POSTGRES_DSN contains placeholder credentials in live mode")
        if not settings.azure_devops_pat:
            problems.append("AZURE_DEVOPS_PAT is required for live Azure DevOps sync")
        if not settings.azure_devops_organization:
            problems.append("AZURE_DEVOPS_ORGANIZATION is required for live sync")
    if not settings.session_secret:
        # demo mode also needs session signing for auth
        problems.append("ENGGRAPH_SESSION_SECRET is required (session signing)")
    if settings.neo4j_password == "":
        problems.append("NEO4J_PASSWORD is empty")
    return problems
