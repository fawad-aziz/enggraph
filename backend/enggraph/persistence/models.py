"""PostgreSQL persistence: users, sessions, org/connector config, sync jobs,
checkpoints, job events, audit records (architecture doc §2.6).

Secret handling rules enforced by this schema:
- Users store a PBKDF2 password hash, never the password.
- Sessions store a SHA-256 hash of the bearer token, never the token.
- Connector configs store a *reference* to the PAT (env var / secret-store
  name). There is no PAT column; the runtime resolves the secret from the
  environment via settings.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JobMode = ("FULL", "INCREMENTAL")
JobState = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED")
UserRoles = ("admin", "viewer")


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16))
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    """Bearer session tokens: only the SHA-256 digest is persisted."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class OrganizationConfig(Base):
    __tablename__ = "organization_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    max_depth: Mapped[int] = mapped_column(Integer, default=4)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ConnectorConfig(Base):
    """Provider connection metadata. `pat_secret_ref` names where the PAT lives
    (e.g. `AZURE_DEVOPS_PAT`); the secret value itself is never stored."""

    __tablename__ = "connector_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="azure_devops")
    ado_organization: Mapped[str] = mapped_column(String(128))
    pat_secret_ref: Mapped[str] = mapped_column(String(128))
    project_names: Mapped[list[Any]] = mapped_column(JSON, default=list[Any])
    page_size: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(default=True)
    last_validated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_validation_ok: Mapped[bool | None] = mapped_column(default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    connector_config_id: Mapped[str] = mapped_column(
        ForeignKey("connector_configs.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="FULL")
    state: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    requested_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    parent_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("sync_jobs.id", ondelete="SET NULL"), default=None
    )
    worker_id: Mapped[str | None] = mapped_column(String(64), default=None)
    nodes_written: Mapped[int] = mapped_column(Integer, default=0)
    edges_written: Mapped[int] = mapped_column(Integer, default=0)
    projects_completed: Mapped[int] = mapped_column(Integer, default=0)
    projects_planned: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.seq"
    )


class JobEvent(Base):
    """Observability: every pipeline stage records one event per job."""

    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("sync_jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    job: Mapped[SyncJob] = relationship(back_populates="events")


class SyncCheckpoint(Base):
    """Per-scope provider position. `provider_version` advances only after the
    corresponding page/batch has been persisted."""

    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("organization_id", "connector_config_id", "scope"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    connector_config_id: Mapped[str] = mapped_column(
        ForeignKey("connector_configs.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(160))  # e.g. "project:Commerce"
    provider_version: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[str | None] = mapped_column(String(40), default=None)
    last_full_pass_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_full_pass_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("sync_jobs.id", ondelete="SET NULL"), default=None
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ImpactAnalysis(Base):
    """Persisted change-impact reports (FR-09), org-scoped and re-readable by id.

    `report` contains graph ids, relationship paths, provenance and the
    limitations section — no credentials, no raw provider payloads.
    """

    __tablename__ = "impact_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    requested_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    subject_id: Mapped[str] = mapped_column(String(160), index=True)
    categories: Mapped[list[Any]] = mapped_column(JSON, default=list[Any])
    depth: Mapped[int] = mapped_column(Integer, default=4)
    limit: Mapped[int] = mapped_column(Integer, default=200)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict[str, Any])
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), index=True)
    actor: Mapped[str] = mapped_column(String(128))  # user id, or "system"
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(160))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict[str, Any])
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
