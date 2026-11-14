"""Sync worker tests: full/incremental runs, checkpoints, reconciliation, failures.

The ingestion path runs against the recorded ADO fixtures through the real
client + normalization, writing through the same `GraphRepository` interface
the production Neo4j repository implements (InMemoryGraphRepository is the
test-only engine, never used by production code).
"""

from __future__ import annotations

import base64

import httpx
import pytest
from ado_fixtures import ORG, PAT, PROJECT, handler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.config import Settings
from enggraph.connectors.azure_devops.client import AdoClient
from enggraph.connectors.errors import ConnectorConfigError
from enggraph.graph.in_memory import InMemoryGraphRepository
from enggraph.graph.model import NodeUpsert
from enggraph.jobs.queue import enqueue_job, job_progress
from enggraph.persistence.models import AuditRecord, Base, ConnectorConfig
from enggraph.sync.pipeline import advance_checkpoint, get_checkpoint, scope_for
from enggraph.sync.worker import production_client_factory, run_once

CONNECTOR_ID = "conn-1"
STALE_ID = "ado:acme:Commerce:workitem:99"


def make_factory(recorder=None):
    def factory(connector: ConnectorConfig) -> AdoClient:
        transport_handler = handler if recorder is None else recorder
        return AdoClient(
            ORG,
            PAT,
            page_size=connector.page_size,
            transport=httpx.MockTransport(transport_handler),
        )

    return factory


@pytest.fixture()
def session(engine) -> Session:
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    session.add(
        ConnectorConfig(
            id=CONNECTOR_ID,
            organization_id=ORG,
            ado_organization=ORG,
            pat_secret_ref="AZURE_DEVOPS_PAT",
            project_names=[PROJECT],
            page_size=2,
        )
    )
    session.commit()
    yield session
    session.close()


def run(session: Session, repo: InMemoryGraphRepository, mode: str = "FULL", factory=None):
    enqueue_job(session, organization_id=ORG, connector_config_id=CONNECTOR_ID, mode=mode)
    return run_once(session, "worker-1", client_factory=factory or make_factory(), repo=repo)


def seed_stale_node(repo: InMemoryGraphRepository) -> None:
    """A provider record last seen years ago; untouched by these fixtures."""
    repo.upsert_nodes(
        [
            NodeUpsert(
                label="WorkItem",
                id=STALE_ID,
                props={"organizationId": ORG, "lastSeenAt": "2020-01-01T00:00:00Z"},
                organization_id=ORG,
            )
        ]
    )


def audit_actions(session: Session) -> list[str]:
    return list(session.execute(select(AuditRecord.action)).scalars().all())


def test_full_sync_writes_the_project_graph(session):
    repo = InMemoryGraphRepository()
    job = run(session, repo)
    assert job.state == "SUCCEEDED"
    assert job.projects_completed == job.projects_planned == 1
    # 14 canonical nodes and 17 edges, matching the normalization contract test
    assert job.nodes_written == 14
    assert job.edges_written == 17
    counts = repo.counts(ORG)
    assert counts["Project"] == 1
    assert counts["WorkItem"] == 2
    assert counts["Developer"] == 4


def test_full_sync_records_every_stage(session):
    repo = InMemoryGraphRepository()
    job = run(session, repo)
    progress = job_progress(session, job.id)
    stages = [e["stage"] for e in progress["events"]]
    assert stages == ["start", "fetch:projects", f"persist:{PROJECT}", "reconcile", "complete"]
    assert progress["nodesWritten"] == 14


def test_full_sync_advances_checkpoint_after_persisting(session):
    repo = InMemoryGraphRepository()
    job = run(session, repo)
    checkpoint = get_checkpoint(session, ORG, CONNECTOR_ID, scope_for(PROJECT))
    assert checkpoint is not None
    assert checkpoint.last_full_pass_job_id == job.id
    assert checkpoint.last_full_pass_at is not None
    assert checkpoint.last_seen_at.endswith("Z")


def test_repeated_full_sync_is_idempotent(session):
    repo = InMemoryGraphRepository()
    run(session, repo)
    before = dict(repo.counts(ORG))
    run(session, repo)
    assert repo.counts(ORG) == before  # upsert by canonical id, no duplicates


def test_missing_project_fails_before_writing(session):
    repo = InMemoryGraphRepository()
    connector = session.get(ConnectorConfig, CONNECTOR_ID)
    connector.project_names = ["Ghost"]
    session.commit()
    job = run(session, repo)
    assert job.state == "FAILED"
    assert "not in the organization" in job.error
    assert repo.counts(ORG) == {}
    assert get_checkpoint(session, ORG, CONNECTOR_ID, "project:Ghost") is None


def test_failed_page_leaves_checkpoint_untouched(session):
    """Auth failure mid-fetch: nothing is persisted, so the position must not move."""

    def failing(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("searchCriteria[skip]", "0"))
        if request.url.path.endswith("/commits") and skip > 0:
            return httpx.Response(401)
        return handler(request)

    repo = InMemoryGraphRepository()
    job = run(session, repo, factory=make_factory(failing))
    assert job.state == "FAILED"
    assert "ConnectorAuthError" in job.error
    assert get_checkpoint(session, ORG, CONNECTOR_ID, scope_for(PROJECT)) is None
    assert repo.counts(ORG) == {}
    events = job_progress(session, job.id)["events"]
    assert events[-1]["level"] == "ERROR"


def test_incremental_sync_advances_checkpoint_to_next_version(session):
    repo = InMemoryGraphRepository()
    job = run(session, repo, mode="INCREMENTAL")
    assert job.state == "SUCCEEDED"
    checkpoint = get_checkpoint(session, ORG, CONNECTOR_ID, scope_for(PROJECT))
    assert checkpoint.provider_version == 6  # fixture nextVersion
    assert checkpoint.last_full_pass_at is None  # not a complete pass
    counts = repo.counts(ORG)
    assert counts["WorkItem"] == 2
    assert counts.get("Project") is None  # changes path writes work items only


def test_incremental_sync_resumes_from_the_checkpoint(session):
    seen: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/changes"):
            seen.append(request.url.params.get("version"))
        return handler(request)

    advance_checkpoint(
        session,
        org_id=ORG,
        connector_id=CONNECTOR_ID,
        scope=scope_for(PROJECT),
        job_id="seed-job",
        provider_version=5,
    )
    repo = InMemoryGraphRepository()
    job = run(session, repo, mode="INCREMENTAL", factory=make_factory(recording))
    assert seen == ["5"]  # provider asked from the stored position, not from 0
    assert get_checkpoint(session, ORG, CONNECTOR_ID, scope_for(PROJECT)).provider_version == 6
    events = job_progress(session, job.id)["events"]
    assert any("since_version=5" in e["message"] for e in events)


def test_full_pass_reconciles_stale_nodes(session):
    repo = InMemoryGraphRepository()
    seed_stale_node(repo)
    job = run(session, repo)
    stale = repo.get_node(STALE_ID, ORG)
    assert stale.props.get("isDeleted") is True
    # reconciled nodes are excluded from live counts
    assert repo.counts(ORG)["WorkItem"] == 2
    reconcile_rows = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "sync.reconcile"))
        .scalars()
        .all()
    )
    assert reconcile_rows[0].details["marked_not_seen"] == 1
    assert reconcile_rows[0].target == job.id


def test_partial_pass_never_marks_anything_deleted(session):
    repo = InMemoryGraphRepository()
    seed_stale_node(repo)
    run(session, repo, mode="INCREMENTAL")
    stale = repo.get_node(STALE_ID, ORG)
    assert stale.props.get("isDeleted") is None
    assert "sync.reconcile" not in audit_actions(session)


def test_organization_mismatch_is_refused_before_any_write(session):
    repo = InMemoryGraphRepository()
    enqueue_job(session, organization_id="other-org", connector_config_id=CONNECTOR_ID, mode="FULL")
    job = run_once(session, "worker-1", client_factory=make_factory(), repo=repo)
    assert job.state == "FAILED"
    assert "does not match connector organization" in job.error
    assert repo.counts(ORG) == {}


def dump_rows(session: Session) -> str:
    chunks: list[str] = []
    for table in Base.metadata.tables.values():
        for row in session.execute(select(table)).all():
            chunks.extend(str(value) for value in row)
    return " ".join(chunks)


def test_no_secret_material_reaches_the_database(session):
    repo = InMemoryGraphRepository()
    run(session, repo)
    stored = dump_rows(session)
    assert PAT not in stored
    assert base64.b64encode(f":{PAT}".encode()).decode() not in stored
    connector = session.get(ConnectorConfig, CONNECTOR_ID)
    assert connector.pat_secret_ref == "AZURE_DEVOPS_PAT"  # reference, not value


def test_secret_reference_is_resolved_at_call_time(monkeypatch):
    secret = "pat-from-environment"
    monkeypatch.setenv("AZURE_DEVOPS_PAT", secret)
    settings = Settings(
        mode="demo", postgres_dsn="sqlite://", session_secret="s" * 32, neo4j_password="pw"
    )
    client = production_client_factory(settings)(
        ConnectorConfig(
            id="c-2",
            organization_id=ORG,
            ado_organization=ORG,
            pat_secret_ref="AZURE_DEVOPS_PAT",
            project_names=[PROJECT],
            page_size=2,
        )
    )
    # httpx normalizes base_url to a prefix path; the organization is the host path.
    assert str(client._http.base_url) == f"https://dev.azure.com/{ORG}/"
    expected = base64.b64encode(f":{secret}".encode()).decode()
    assert client._http.headers["authorization"] == f"Basic {expected}"
    client.close()


def test_missing_secret_reference_is_a_config_error(monkeypatch):
    monkeypatch.delenv("AZURE_DEVOPS_SIDE_PAT", raising=False)
    settings = Settings(
        mode="demo", postgres_dsn="sqlite://", session_secret="s" * 32, neo4j_password="pw"
    )
    factory = production_client_factory(settings)
    connector = ConnectorConfig(
        id="c-3",
        organization_id=ORG,
        ado_organization=ORG,
        pat_secret_ref="AZURE_DEVOPS_SIDE_PAT",
        project_names=[PROJECT],
        page_size=2,
    )
    with pytest.raises(ConnectorConfigError, match="not set"):
        factory(connector)


def test_run_once_returns_none_when_the_queue_is_empty(session):
    repo = InMemoryGraphRepository()
    assert run_once(session, "worker-1", client_factory=make_factory(), repo=repo) is None
