"""Sync job endpoints (FR-02, FR-10): enqueue, observe, retry.

Observability is part of the contract: a job reports its state, attempt,
per-stage events, and write counts, so a failing sync is diagnosable from the
API alone. Jobs are listed and read only inside the caller's organization.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from enggraph.api.deps import (
    bounded_limit,
    bounded_offset,
    get_principal,
    get_session,
    require_admin,
)
from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal
from enggraph.jobs.queue import audit_record, enqueue_job, job_progress, retry_job
from enggraph.persistence.models import ConnectorConfig, SyncJob

router = APIRouter(prefix="/api/v1/sync-jobs", tags=["sync-jobs"])
VALID_MODES = ("FULL", "INCREMENTAL")


class EnqueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectorConfigId: str
    mode: str = "FULL"

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in VALID_MODES:
            raise ValueError(f"mode must be one of {', '.join(VALID_MODES)}")
        return normalized


def _job_dto(job: SyncJob) -> dict[str, Any]:
    return {
        "jobId": job.id,
        "connectorConfigId": job.connector_config_id,
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
        "requestedBy": job.requested_by,
        "createdAt": job.created_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
    }


def _load_org_job(session: Session, principal: Principal, job_id: str) -> SyncJob:
    """Cross-organization jobs read as 404, never as 403 (no enumeration)."""
    job = session.get(SyncJob, job_id)
    if job is None or job.organization_id != principal.organization_id:
        raise ApiError("job_not_found", f"job {job_id} not found", 404)
    return job


@router.post("", status_code=201)
def enqueue(
    body: EnqueueRequest,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    """Queue a sync job. Admin-only: a queued job writes into the graph store."""
    connector = session.get(ConnectorConfig, body.connectorConfigId)
    if connector is None or connector.organization_id != principal.organization_id:
        raise ApiError("connector_not_found", "connector configuration not found", 404)
    job = enqueue_job(
        session,
        organization_id=principal.organization_id,
        connector_config_id=connector.id,
        mode=body.mode,
        requested_by=principal.user_id,
    )
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="sync.enqueue",
        target=job.id,
        details={"mode": job.mode, "connector_config_id": connector.id},
    )
    return job_progress(session, job.id)


@router.get("")
def list_jobs(
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
    limit: Annotated[int, Depends(bounded_limit)],
    offset: Annotated[int, Depends(bounded_offset)],
) -> dict[str, Any]:
    scoped = select(SyncJob).where(SyncJob.organization_id == principal.organization_id)
    total = session.execute(
        select(func.count())
        .select_from(SyncJob)
        .where(SyncJob.organization_id == principal.organization_id)
    ).scalar_one()
    jobs = (
        session.execute(
            scoped.order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {
        "items": [_job_dto(j) for j in jobs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{job_id}")
def get_job(
    job_id: str,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    _load_org_job(session, principal, job_id)
    return job_progress(session, job_id)


@router.post("/{job_id}/retry", status_code=201)
def retry(
    job_id: str,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    """Retry is a *new* queued job linked to the failed parent (history kept)."""
    _load_org_job(session, principal, job_id)
    try:
        new_job = retry_job(session, job_id, requested_by=principal.user_id)
    except ValueError as exc:
        raise ApiError("job_not_retryable", str(exc), 409) from exc
    return job_progress(session, new_job.id)


@router.post("/{job_id}/cancel", status_code=200)
def cancel(
    job_id: str,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    from enggraph.jobs.queue import cancel_job

    job = _load_org_job(session, principal, job_id)
    try:
        canceled = cancel_job(session, job)
    except ValueError as exc:
        raise ApiError("job_not_cancellable", str(exc), 409) from exc
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="sync.cancel",
        target=canceled.id,
        details={"state": canceled.state},
    )
    return job_progress(session, canceled.id)
