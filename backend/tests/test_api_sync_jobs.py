"""Sync job API tests (FR-02, FR-10).

The seeded graph comes from one real sync run against the recorded fixtures, so
job observability is exercised on genuine worker output (states, per-stage
events, write counts). Enqueue/retry/cancel are admin-only; jobs are read only
inside the caller's organization, and a cross-organization job reads as 404.
"""

from __future__ import annotations

import httpx
import pytest
from ado_fixtures import ORG, handler
from api_support import (
    ADMIN_EMAIL,
    CONNECTOR_ID,
    OTHER_ADMIN_EMAIL,
    OTHER_ORG,
    VIEWER_EMAIL,
    add_user,
    api_client_factory,
    bearer,
    build_app,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.jobs.queue import enqueue_job
from enggraph.persistence.models import AuditRecord, SyncJob, User
from enggraph.sync.worker import run_once


@pytest.fixture()
def seeded(settings, engine, fake_driver):
    app, repo = build_app(settings, engine, fake_driver)
    add_user(engine, ADMIN_EMAIL, "admin", ORG)
    add_user(engine, VIEWER_EMAIL, "viewer", ORG)
    add_user(engine, OTHER_ADMIN_EMAIL, "admin", OTHER_ORG)
    return app, repo


@pytest.fixture()
def client(seeded):
    app, _ = seeded
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def db(engine) -> Session:
    return sessionmaker(engine, class_=Session, expire_on_commit=False)()


def user_id(engine, email: str) -> str:
    session = db(engine)
    uid = session.execute(select(User).where(User.email == email)).scalar_one().id
    session.close()
    return uid


def mark_state(engine, job_id: str, state: str, error: str | None = None) -> None:
    """Drive a job into a state the worker did not produce, to exercise the
    retry/cancel contract at the API layer."""
    session = db(engine)
    job = session.get(SyncJob, job_id)
    job.state = state
    if error is not None:
        job.error = error
    session.commit()
    session.close()


def test_seeded_job_is_visible_with_events_and_write_counts(client):
    token = login(client, ADMIN_EMAIL)
    listing = client.get("/api/v1/sync-jobs", headers=bearer(token)).json()
    assert listing["total"] == 1
    seeded_job = listing["items"][0]
    assert seeded_job["state"] == "SUCCEEDED"
    assert seeded_job["mode"] == "FULL"
    assert seeded_job["connectorConfigId"] == CONNECTOR_ID
    detail = client.get(f"/api/v1/sync-jobs/{seeded_job['jobId']}", headers=bearer(token)).json()
    # Observability contract: a completed job explains itself.
    assert detail["nodesWritten"] == 14
    assert detail["edgesWritten"] == 17
    assert detail["projectsCompleted"] == 1
    assert detail["projectsPlanned"] == 1
    assert detail["attempt"] == 1
    assert detail["workerId"] == "worker-1"
    stages = [e["stage"] for e in detail["events"]]
    assert stages[0] == "start"
    assert any(s.startswith("persist:") for s in stages)
    assert all(e["seq"] > 0 for e in detail["events"])


def test_enqueue_is_admin_only(client):
    token = login(client, VIEWER_EMAIL)
    resp = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID, "mode": "INCREMENTAL"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_required"


def test_enqueue_queues_a_job_and_audits_it(client, engine):
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID, "mode": "incremental"},
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["state"] == "QUEUED"
    assert body["mode"] == "INCREMENTAL"
    assert body["attempt"] == 0
    assert body["parentJobId"] is None
    assert body["requestedBy"] == user_id(engine, ADMIN_EMAIL)
    assert body["organizationId"] == ORG
    session = db(engine)
    audit = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "sync.enqueue"))
        .scalars()
        .all()
    )
    assert audit[0].organization_id == ORG
    assert audit[0].target == body["jobId"]
    assert audit[0].details["mode"] == "INCREMENTAL"
    session.close()


def test_enqueue_rejects_an_unknown_mode(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID, "mode": "RELOAD"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_enqueue_rejects_a_connector_from_another_organization(client):
    """Cross-organization write reads as 404, not 403: no connector enumeration."""
    token = login(client, OTHER_ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connector_not_found"


def test_job_list_is_scoped_to_the_callers_organization(client):
    token = login(client, ADMIN_EMAIL)
    client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID},
    )
    other = login(client, OTHER_ADMIN_EMAIL)
    listing = client.get("/api/v1/sync-jobs", headers=bearer(other)).json()
    assert listing["total"] == 0
    assert listing["items"] == []


def test_job_list_is_paginated_newest_first(client):
    token = login(client, ADMIN_EMAIL)
    seeded_id = client.get("/api/v1/sync-jobs", headers=bearer(token)).json()["items"][0]["jobId"]
    ids = []
    for _ in range(3):
        resp = client.post(
            "/api/v1/sync-jobs",
            headers=bearer(token),
            json={"connectorConfigId": CONNECTOR_ID},
        )
        ids.append(resp.json()["jobId"])
    listing = client.get("/api/v1/sync-jobs?limit=2", headers=bearer(token)).json()
    assert listing["total"] == 4
    assert listing["limit"] == 2
    # Newest first: the three enqueued jobs, most recent at the top.
    assert [j["jobId"] for j in listing["items"]] == list(reversed(ids))[:2]
    page2 = client.get("/api/v1/sync-jobs?limit=2&offset=2", headers=bearer(token)).json()
    assert [j["jobId"] for j in page2["items"]] == [ids[0], seeded_id]
    assert page2["total"] == 4
    assert page2["offset"] == 2


def test_job_of_another_organization_is_not_readable(client):
    token = login(client, ADMIN_EMAIL)
    job_id = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID},
    ).json()["jobId"]
    other = login(client, OTHER_ADMIN_EMAIL)
    resp = client.get(f"/api/v1/sync-jobs/{job_id}", headers=bearer(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "job_not_found"


def test_retry_of_a_succeeded_job_is_refused(client):
    token = login(client, ADMIN_EMAIL)
    seeded_job = client.get("/api/v1/sync-jobs", headers=bearer(token)).json()["items"][0]
    resp = client.post(f"/api/v1/sync-jobs/{seeded_job['jobId']}/retry", headers=bearer(token))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "job_not_retryable"


def test_retry_of_a_failed_job_creates_a_linked_queued_job(client, engine):
    token = login(client, ADMIN_EMAIL)
    failed = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID, "mode": "INCREMENTAL"},
    ).json()["jobId"]
    mark_state(engine, failed, "FAILED", "provider rejected the request")
    resp = client.post(f"/api/v1/sync-jobs/{failed}/retry", headers=bearer(token))
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["state"] == "QUEUED"
    assert body["parentJobId"] == failed
    assert body["attempt"] == 1
    assert body["mode"] == "INCREMENTAL"
    session = db(engine)
    audit = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "sync.retry"))
        .scalars()
        .all()
    )
    assert audit[0].target == failed
    assert audit[0].details["new_job_id"] == body["jobId"]
    session.close()


def test_cancel_marks_a_queued_job_and_audits_it(client, engine):
    token = login(client, ADMIN_EMAIL)
    queued = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID},
    ).json()["jobId"]
    resp = client.post(f"/api/v1/sync-jobs/{queued}/cancel", headers=bearer(token))
    assert resp.status_code == 200, resp.json()
    assert resp.json()["state"] == "CANCELED"
    session = db(engine)
    audit = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "sync.cancel"))
        .scalars()
        .all()
    )
    assert audit[0].target == queued
    assert audit[0].details["state"] == "CANCELED"
    session.close()


def test_cancel_of_a_completed_job_is_refused(client):
    token = login(client, ADMIN_EMAIL)
    seeded_job = client.get("/api/v1/sync-jobs", headers=bearer(token)).json()["items"][0]
    resp = client.post(f"/api/v1/sync-jobs/{seeded_job['jobId']}/cancel", headers=bearer(token))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "job_not_cancellable"


def test_enqueue_does_not_run_the_worker(client):
    """Enqueueing is a queue write; graph state stays untouched until a worker runs."""
    token = login(client, ADMIN_EMAIL)
    before = client.get("/api/v1/organizations", headers=bearer(token)).json()
    client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID},
    )
    after = client.get("/api/v1/organizations", headers=bearer(token)).json()
    assert before["items"][0]["graphCounts"] == after["items"][0]["graphCounts"]


def test_queued_job_is_picked_up_by_a_worker_run(client, engine, seeded):
    """The enqueued job is real queue state: a worker run claims and completes it."""
    _, repo = seeded
    token = login(client, ADMIN_EMAIL)
    queued = client.post(
        "/api/v1/sync-jobs",
        headers=bearer(token),
        json={"connectorConfigId": CONNECTOR_ID, "mode": "FULL"},
    ).json()["jobId"]
    session = db(engine)
    transport = httpx.MockTransport(handler)
    run_once(session, "worker-2", client_factory=api_client_factory(transport), repo=repo)
    session.close()
    detail = client.get(f"/api/v1/sync-jobs/{queued}", headers=bearer(token)).json()
    assert detail["state"] == "SUCCEEDED"
    assert detail["workerId"] == "worker-2"
    assert detail["nodesWritten"] == 14


def test_job_queue_write_requires_authentication(client):
    resp = client.post("/api/v1/sync-jobs", json={"connectorConfigId": CONNECTOR_ID})
    assert resp.status_code == 401


def test_enqueue_job_helper_state_matches_api_view(client, engine):
    """API view and queue helper agree on the same row (no shadow state)."""
    session = db(engine)
    job = enqueue_job(
        session,
        organization_id=ORG,
        connector_config_id=CONNECTOR_ID,
        mode="FULL",
        requested_by="seed",
    )
    session.close()
    token = login(client, ADMIN_EMAIL)
    detail = client.get(f"/api/v1/sync-jobs/{job.id}", headers=bearer(token)).json()
    assert detail["jobId"] == job.id
    assert detail["state"] == "QUEUED"
