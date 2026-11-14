"""Job queue tests: enqueue, exclusive claim, events, retry, cancel, audit.

The claim guard (`UPDATE ... WHERE state='QUEUED'` + rowcount check) is the
correctness mechanism; these tests exercise it, including the losing side of a
concurrent claim.
"""

from __future__ import annotations

import base64

import pytest
import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from enggraph.jobs.queue import (
    audit_record,
    cancel_job,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    job_progress,
    record_event,
    retry_job,
)
from enggraph.persistence.models import AuditRecord, Base, ConnectorConfig, JobEvent, SyncJob

ORG = "acme"
PAT = "pat-abc123def456"


@pytest.fixture()
def session(engine) -> Session:
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    session.add(ConnectorConfig(
        id="conn-1", organization_id=ORG, ado_organization=ORG,
        pat_secret_ref="AZURE_DEVOPS_PAT", project_names=["Commerce"],
    ))
    session.commit()
    yield session
    session.close()


def queue(session, mode="FULL", parent=None):
    return enqueue_job(
        session, organization_id=ORG, connector_config_id="conn-1",
        mode=mode, parent_job_id=parent,
    )


def test_enqueue_queues_a_new_job(session):
    job = queue(session)
    assert job.state == "QUEUED"
    assert job.attempt == 0
    assert session.execute(
        sa.select(sa.func.count()).select_from(SyncJob).where(SyncJob.state == "QUEUED")
    ).scalar() == 1


def test_invalid_mode_is_rejected(session):
    with pytest.raises(ValueError, match="invalid job mode"):
        queue(session, mode="SOMETHING")


def test_claim_marks_running_and_counts_attempt(session):
    job = queue(session)
    claimed = claim_next_job(session, "worker-1")
    assert claimed.id == job.id
    assert claimed.state == "RUNNING"
    assert claimed.worker_id == "worker-1"
    assert claimed.attempt == 1  # claim = first execution attempt
    assert claimed.started_at is not None


def test_claim_returns_none_when_queue_is_empty(session):
    assert claim_next_job(session, "worker-1") is None


def test_claim_is_exclusive(session):
    job = queue(session)
    first = claim_next_job(session, "worker-a")
    assert first.id == job.id
    assert claim_next_job(session, "worker-b") is None  # no QUEUED row left


def test_losing_side_of_a_race_hits_the_state_guard(session):
    """The guarded update, not the SELECT, decides who owns a job."""
    job = queue(session)
    seen_id = session.execute(
        select(SyncJob.id).where(SyncJob.state == "QUEUED")
    ).scalar_one()
    assert seen_id == job.id
    claim_next_job(session, "worker-b")  # lands between A's select and update
    result = session.execute(
        update(SyncJob)
        .where(SyncJob.id == job.id, SyncJob.state == "QUEUED")
        .values(state="RUNNING", worker_id="worker-a")
    )
    assert result.rowcount == 0
    assert session.get(SyncJob, job.id).worker_id == "worker-b"


def test_events_are_sequenced_and_redacted(session):
    job = queue(session)
    record_event(session, job, "start", "mode=FULL projects=1")
    credentials = base64.b64encode(f":{PAT}".encode()).decode()
    record_event(session, job, "fetch", f"Authorization: Basic {credentials}")
    events = session.execute(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.seq)
    ).scalars().all()
    assert [e.seq for e in events] == [1, 2]
    assert [e.stage for e in events] == ["start", "fetch"]
    assert PAT not in events[1].message
    assert credentials not in events[1].message


def test_failure_keeps_partial_progress_and_redacts_error(session):
    job = queue(session)
    claim_next_job(session, "worker-1")
    fail_job(session, job, f"Authorization: Basic {PAT}", projects_completed=1)
    assert job.state == "FAILED"
    assert job.projects_completed == 1
    assert PAT not in job.error


def test_retry_creates_a_linked_job_with_a_new_attempt(session):
    original = queue(session)
    claim_next_job(session, "worker-1")
    fail_job(session, original, "provider unavailable", projects_completed=0)
    retry = retry_job(session, original.id, requested_by=None)
    assert retry.state == "QUEUED"
    assert retry.attempt == original.attempt + 1
    assert retry.parent_job_id == original.id
    audit = session.execute(
        select(AuditRecord).where(AuditRecord.action == "sync.retry")
    ).scalars().all()
    assert audit[0].target == original.id
    assert audit[0].details["new_job_id"] == retry.id


def test_retry_is_refused_for_non_failed_jobs(session):
    job = queue(session)
    claim_next_job(session, "worker-1")
    complete_job(session, job, nodes_written=3, edges_written=2, projects_completed=1)
    with pytest.raises(ValueError, match="only failed/canceled jobs"):
        retry_job(session, job.id)


def test_cancel_only_applies_to_queued_or_running(session):
    queued = queue(session)
    cancel_job(session, queued)
    assert queued.state == "CANCELED"
    running = queue(session)
    claim_next_job(session, "worker-1")
    complete_job(session, running, nodes_written=0, edges_written=0, projects_completed=0)
    with pytest.raises(ValueError, match="only queued/running jobs"):
        cancel_job(session, running)


def test_audit_details_never_store_secrets(session):
    audit_record(
        session, organization_id=ORG, actor="user-1", action="connector.update",
        target="conn-1", details={"pat": PAT, "page_size": 100},
    )
    stored = session.execute(select(AuditRecord)).scalars().all()[0]
    assert PAT not in str(stored.details)
    assert stored.details["page_size"] == 100


def test_job_progress_is_a_camel_case_dto_with_ordered_events(session):
    job = queue(session)
    claim_next_job(session, "worker-1")
    record_event(session, job, "start", "mode=FULL projects=1")
    record_event(session, job, "persist:Commerce", "nodes=13 edges=17")
    progress = job_progress(session, job.id)
    assert progress["jobId"] == job.id
    assert progress["state"] == "RUNNING"
    assert [e["seq"] for e in progress["events"]] == [1, 2]
    assert progress["events"][1]["stage"] == "persist:Commerce"
    assert job_progress(session, "missing-job") == {}
