"""Shared API test support.

API tests run the real request path — auth, authorization, domain services,
graph writes — through the production routers, with two test doubles injected
through the app context:

- `InMemoryGraphRepository` (test-only engine, same semantics as Neo4j),
- `httpx.MockTransport` serving the recorded Azure DevOps fixtures.

The seeded graph is produced by the real sync worker against those fixtures,
so the API is exercised on ingested provider data, not hand-built rows.
"""

from __future__ import annotations

import httpx
from ado_fixtures import ORG, PAT, PROJECT, handler
from sqlalchemy.orm import Session, sessionmaker

from enggraph.auth.service import create_user
from enggraph.connectors.azure_devops.client import AdoClient
from enggraph.graph.in_memory import InMemoryGraphRepository
from enggraph.jobs.queue import enqueue_job
from enggraph.main import create_app
from enggraph.persistence.models import Base, ConnectorConfig
from enggraph.sync.worker import run_once

CONNECTOR_ID = "conn-1"
ADMIN_EMAIL = "admin@acme.test"
VIEWER_EMAIL = "viewer@acme.test"
OTHER_ADMIN_EMAIL = "admin@other.test"
OTHER_ORG = "other"
PASSWORD = "horse-battery-9"

# Canonical ids present in the seeded graph (fixture project "Commerce").
PROJECT_ID = "ado:acme:project:p-uuid-1"
REPO_ID = "ado:acme:Commerce:repository:r-uuid-1"
TEAM_ID = "ado:acme:team:t-uuid-1"
DEVELOPER_ID = "ado:acme:identity:i-uuid-1"


def api_client_factory(transport: httpx.BaseTransport):
    """Client factory with the mock transport, PAT from the recorded fixture."""

    def factory(connector: ConnectorConfig) -> AdoClient:
        return AdoClient(
            connector.ado_organization,
            PAT,
            page_size=connector.page_size,
            transport=transport,
        )

    return factory


def add_user(engine, email: str, role: str, org: str):
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    user = create_user(
        session,
        email=email,
        password=PASSWORD,
        role=role,
        organization_id=org,
        actor="seed",
    )
    session.close()
    return user


def seed_graph(session: Session, repo: InMemoryGraphRepository, transport) -> None:
    """Run one real full sync against the recorded fixtures."""
    enqueue_job(session, organization_id=ORG, connector_config_id=CONNECTOR_ID, mode="FULL")
    run_once(session, "worker-1", client_factory=api_client_factory(transport), repo=repo)


def build_app(
    settings, engine, fake_driver, *, seed: bool = True, transport_handler=handler
) -> tuple:
    """App with an injected graph engine and mock ADO transport."""
    transport = httpx.MockTransport(transport_handler)
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
    repo = InMemoryGraphRepository()
    if seed:
        seed_graph(session, repo, transport)
    session.close()
    app = create_app(
        settings=settings,
        engine=engine,
        driver=fake_driver,
        repo=repo,
        client_factory=api_client_factory(transport),
        ado_transport=transport,
    )
    return app, repo


def login(client, email: str, password: str = PASSWORD) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    return resp.json()["token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
