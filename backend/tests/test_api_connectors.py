"""Connector + organization API tests.

Validation and live project listing go through the real Azure DevOps client;
responses come from the recorded fixtures via the injected mock transport. The
PAT is resolved from its reference at call time and must never appear in a
response, in stored rows, or in the audit trail.
"""

from __future__ import annotations

import httpx
import pytest
from ado_fixtures import ORG, PAT
from api_support import (
    ADMIN_EMAIL,
    CONNECTOR_ID,
    OTHER_ADMIN_EMAIL,
    OTHER_ORG,
    VIEWER_EMAIL,
    add_user,
    bearer,
    build_app,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.persistence.models import AuditRecord, Base, ConnectorConfig


@pytest.fixture()
def seeded(settings, engine, fake_driver):
    # build_app creates the schema, so users are added after it.
    app, repo = build_app(settings, engine, fake_driver)
    add_user(engine, ADMIN_EMAIL, "admin", ORG)
    add_user(engine, VIEWER_EMAIL, "viewer", ORG)
    add_user(engine, OTHER_ADMIN_EMAIL, "admin", OTHER_ORG)
    return app, repo


@pytest.fixture()
def client(seeded):
    app, repo = seeded
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_organizations_endpoint_is_scoped_to_the_callers_org(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.get("/api/v1/organizations", headers=bearer(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["organizationId"] == ORG
    assert item["graphCounts"]["Project"] == 1
    assert item["graphCounts"]["Developer"] == 4


def test_organizations_requires_authentication(client):
    resp = client.get("/api/v1/organizations")
    assert resp.status_code == 401


def test_validate_reaches_the_provider_through_the_real_client(client, monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/connectors/azure-devops/validate",
        headers=bearer(token),
        json={"adoOrganization": ORG, "patSecretRef": "AZURE_DEVOPS_PAT"},
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["reachable"] is True
    # First page of the fixture project list (mocked transport, real client paging).
    assert body["sampleProjectNames"] == ["Commerce", "Data"]
    assert body["secretReference"] == "AZURE_DEVOPS_PAT"
    # The response reports the reference, never the secret value.
    assert PAT not in resp.text


def test_validate_is_admin_only(client, monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, VIEWER_EMAIL)
    resp = client.post(
        "/api/v1/connectors/azure-devops/validate",
        headers=bearer(token),
        json={"adoOrganization": ORG, "patSecretRef": "AZURE_DEVOPS_PAT"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_required"


def test_missing_secret_reference_is_reported_as_a_config_problem(client, monkeypatch):
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/connectors/azure-devops/validate",
        headers=bearer(token),
        json={"adoOrganization": ORG, "patSecretRef": "AZURE_DEVOPS_PAT"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "connector_failure"
    assert "AZURE_DEVOPS_PAT" in body["error"]["message"]


def test_permission_error_surfaces_as_a_provider_failure(
    settings, engine, fake_driver, monkeypatch
):
    """A 403 from the provider is a typed connector failure, not a 500."""

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"value": "TF400817", "message": "bad permission"})

    app, _ = build_app(settings, engine, fake_driver, seed=False, transport_handler=denied)
    add_user(engine, ADMIN_EMAIL, "admin", ORG)
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    with TestClient(app, raise_server_exceptions=False) as client:
        token = login(client, ADMIN_EMAIL)
        resp = client.post(
            "/api/v1/connectors/azure-devops/validate",
            headers=bearer(token),
            json={"adoOrganization": ORG, "patSecretRef": "AZURE_DEVOPS_PAT"},
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "connector_failure"
        # The message is the provider's, redacted; the PAT never appears.
        assert PAT not in resp.text


def test_configure_stores_a_reference_and_audits_the_write(client, monkeypatch, engine):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/connectors/azure-devops/configure",
        headers=bearer(token),
        json={
            "adoOrganization": ORG,
            "patSecretRef": "AZURE_DEVOPS_PAT",
            "projectNames": ["Commerce", "Data"],
            "pageSize": 2,
        },
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["patSecretRef"] == "AZURE_DEVOPS_PAT"
    assert body["projectNames"] == ["Commerce", "Data"]
    assert body["lastValidationOk"] is True
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    row = session.get(ConnectorConfig, CONNECTOR_ID)
    assert row.pat_secret_ref == "AZURE_DEVOPS_PAT"
    assert PAT not in str(row.project_names)
    audit = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "connector.configure"))
        .scalars()
        .all()
    )
    assert audit[0].organization_id == ORG
    assert audit[0].details["secretReference"] == "AZURE_DEVOPS_PAT"
    session.close()


def test_configure_rejects_an_empty_project_list(client, monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/connectors/azure-devops/configure",
        headers=bearer(token),
        json={"adoOrganization": ORG, "patSecretRef": "AZURE_DEVOPS_PAT", "projectNames": []},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_projects_are_listed_live_from_the_configured_connector(client, monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, ADMIN_EMAIL)
    resp = client.get("/api/v1/connectors/azure-devops/projects", headers=bearer(token))
    assert resp.status_code == 200, resp.json()
    names = [p["name"] for p in resp.json()["items"]]
    # continuation-token paging across both fixture pages
    assert names == ["Commerce", "Data", "Ops"]


def test_projects_are_not_visible_from_another_organization(client, monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, OTHER_ADMIN_EMAIL)
    resp = client.get("/api/v1/connectors/azure-devops/projects", headers=bearer(token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connector_not_configured"


def test_unknown_secret_reference_name_is_rejected(client, monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/connectors/azure-devops/validate",
        headers=bearer(token),
        json={"adoOrganization": ORG, "patSecretRef": "not an env name"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_configure_never_persists_the_secret_value(client, monkeypatch, engine):
    monkeypatch.setenv("AZURE_DEVOPS_PAT", PAT)
    token = login(client, ADMIN_EMAIL)
    client.post(
        "/api/v1/connectors/azure-devops/configure",
        headers=bearer(token),
        json={
            "adoOrganization": ORG,
            "patSecretRef": "AZURE_DEVOPS_PAT",
            "projectNames": ["Commerce"],
        },
    )
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    dump = " ".join(
        str(value)
        for table in Base.metadata.tables.values()
        for row in session.execute(select(table)).all()
        for value in row
    )
    assert PAT not in dump
    session.close()
