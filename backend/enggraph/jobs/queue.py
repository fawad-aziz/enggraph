"""DB-backed sync job queue (Postgres) and job lifecycle.

Claim semantics: the queue is a plain table. A worker claims the oldest
QUEUED job with a single `UPDATE ... WHERE state='QUEUED'` guard and checks
rowcount, so exactly one worker wins each job. `SELECT ... FOR UPDATE SKIP
LOCKED` is a Postgres throughput optimization on top of this; correctness
comes from the guarded update, which is what the tests exercise.

Checkpoint rule: `provider_version` advances only after the page/batch it
describes has been persisted (architecture doc §4).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from enggraph.logging_setup import SENSITIVE_KEY_NAMES, get_logger, redact
from enggraph.persistence.models import (
    AuditRecord,
    JobEvent,
    SyncJob,
    utcnow,
)

log = get_logger("enggraph.jobs")


def new_id() -> str:
    return str(uuid.uuid4())


def enqueue_job(
    session: Session,
    *,
    organization_id: str,
    connector_config_id: str,
    mode: str,
    requested_by: str | None = None,
    parent_job_id: str | None = None,
) -> SyncJob:
    """Queue a sync job. Retries are new jobs linked to the failed parent."""
    if mode not in ("FULL", "INCREMENTAL"):
        raise ValueError(f"invalid job mode: {mode}")
    attempt = 0
    if parent_job_id is not None:
        parent = session.get(SyncJob, parent_job_id)
        if parent is None:
            raise ValueError(f"unknown parent job: {parent_job_id}")
        attempt = parent.attempt + 1
    job = SyncJob(
        id=new_id(),
        organization_id=organization_id,
        connector_config_id=connector_config_id,
        mode=mode,
        state="QUEUED",
        requested_by=requested_by,
        attempt=attempt,
        parent_job_id=parent_job_id,
        projects_planned=0,
    )
    session.add(job)
    session.commit()
    return job


def claim_next_job(session: Session, worker_id: str) -> SyncJob | None:
    """Claim the oldest queued job. Returns None when the queue is empty."""
    queued_id = session.execute(
        select(SyncJob.id)
        .where(SyncJob.state == "QUEUED")
        .order_by(SyncJob.created_at, SyncJob.id)
        .limit(1)
    ).scalar_one_or_none()
    if queued_id is None:
        return None
    result = session.execute(
        update(SyncJob)
        .where(SyncJob.id == queued_id, SyncJob.state == "QUEUED")
        .values(
            state="RUNNING", worker_id=worker_id, started_at=utcnow(), attempt=SyncJob.attempt + 1
        )
    )
    # `Session.execute` is typed as a plain Result; an ORM bulk UPDATE yields a
    # CursorResult, which is where `rowcount` lives.
    if cast(CursorResult[Any], result).rowcount != 1:
        # Another worker claimed it first (state no longer QUEUED).
        session.rollback()
        return None
    session.commit()
    job = session.get(SyncJob, queued_id)
    session.refresh(job)
    return job


def record_event(
    session: Session,
    job: SyncJob,
    stage: str,
    message: str,
    level: str = "INFO",
) -> JobEvent:
    """Append an observable pipeline stage event. Message is redacted."""
    last_seq = session.execute(
        select(JobEvent.seq).where(JobEvent.job_id == job.id).order_by(JobEvent.seq.desc()).limit(1)
    ).scalar_one_or_none()
    event = JobEvent(
        id=new_id(),
        job_id=job.id,
        seq=(last_seq or 0) + 1,
        stage=stage,
        level=level,
        message=redact(message),
    )
    session.add(event)
    session.commit()
    return event


def complete_job(
    session: Session,
    job: SyncJob,
    *,
    nodes_written: int,
    edges_written: int,
    projects_completed: int,
) -> SyncJob:
    job.state = "SUCCEEDED"
    job.nodes_written = nodes_written
    job.edges_written = edges_written
    job.projects_completed = projects_completed
    job.finished_at = utcnow()
    session.commit()
    return job


def fail_job(session: Session, job: SyncJob, error: str, *, projects_completed: int) -> SyncJob:
    """Failure keeps projects_completed visible; checkpoints stay untouched."""
    job.state = "FAILED"
    job.error = redact(error)[:2000]
    job.projects_completed = projects_completed
    job.finished_at = utcnow()
    session.commit()
    return job


def cancel_job(session: Session, job: SyncJob) -> SyncJob:
    if job.state not in ("QUEUED", "RUNNING"):
        raise ValueError(f"only queued/running jobs can be canceled (state={job.state})")
    job.state = "CANCELED"
    job.finished_at = utcnow()
    session.commit()
    return job


def retry_job(session: Session, job_id: str, requested_by: str | None = None) -> SyncJob:
    """Retry = a new queued job linked to the failed one (history is preserved)."""
    original = session.get(SyncJob, job_id)
    if original is None:
        raise ValueError(f"unknown job: {job_id}")
    if original.state not in ("FAILED", "CANCELED"):
        raise ValueError(f"only failed/canceled jobs can be retried (state={original.state})")
    new_job = enqueue_job(
        session,
        organization_id=original.organization_id,
        connector_config_id=original.connector_config_id,
        mode=original.mode,
        requested_by=requested_by,
        parent_job_id=original.id,
    )
    audit_record(
        session,
        organization_id=original.organization_id,
        actor=requested_by or "system",
        action="sync.retry",
        target=original.id,
        details={"new_job_id": new_job.id, "attempt": new_job.attempt},
    )
    return new_job


def audit_record(
    session: Session,
    *,
    organization_id: str,
    actor: str,
    action: str,
    target: str,
    details: dict[str, Any] | None = None,
) -> AuditRecord:
    record = AuditRecord(
        id=new_id(),
        organization_id=organization_id,
        actor=actor,
        action=action,
        target=redact(target),
        details=redact_details(details or {}),
    )
    session.add(record)
    session.commit()
    return record


def redact_details(details: dict[str, Any]) -> dict[str, Any]:
    """Redact audit payloads (secrets never enter the audit trail).

    Values under a sensitive key are dropped entirely; other string values go
    through `redact()`, which strips credential-shaped text (headers, tokens).
    """
    result: dict[str, Any] = {}
    for key, value in details.items():
        if key in SENSITIVE_KEY_NAMES:
            result[key] = "[REDACTED]"
        elif isinstance(value, str):
            result[key] = redact(value)
        else:
            result[key] = value
    return result


def job_progress(session: Session, job_id: str) -> dict[str, Any]:
    """Job + ordered events, used by the sync-jobs API (milestone 5)."""
    job = session.get(SyncJob, job_id)
    if job is None:
        return {}
    events = (
        session.execute(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq))
        .scalars()
        .all()
    )
    return {
        "jobId": job.id,
        "organizationId": job.organization_id,
        "connectorConfigId": job.connector_config_id,
        "requestedBy": job.requested_by,
        "mode": job.mode,
        "state": job.state,
        "attempt": job.attempt,
        "parentJobId": job.parent_job_id,
        "workerId": job.worker_id,
        "nodesWritten": job.nodes_written,
        "edgesWritten": job.edges_written,
        "projectsCompleted": job.projects_completed,
        "projectsPlanned": job.projects_planned,
        "error": job.error,
        "createdAt": job.created_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "events": [
            {"seq": e.seq, "stage": e.stage, "level": e.level, "message": e.message} for e in events
        ],
    }
