"""Engineering catalog API tests (FR-06).

The catalog is the only user-writable surface of the graph. These tests pin the
rules that make it safe to write: admin-only mutation, manual provenance on
every manual edge, the manual relationship allowlist, endpoint type validation,
provider-owned edges not deletable, and an audit record for every write.
"""

from __future__ import annotations

import pytest
from ado_fixtures import ORG
from api_support import (
    ADMIN_EMAIL,
    DEVELOPER_ID,
    OTHER_ADMIN_EMAIL,
    OTHER_ORG,
    REPO_ID,
    TEAM_ID,
    VIEWER_EMAIL,
    add_user,
    bearer,
    build_app,
    login,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.graph.model import EdgeUpsert
from enggraph.persistence.models import AuditRecord

PR_ID = "ado:acme:Commerce:pullrequest:r-uuid-1:7"
COMMIT_ABC = "ado:acme:Commerce:commit:r-uuid-1:abc123"


@pytest.fixture()
def seeded(settings, engine, fake_driver):
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


def audits(engine, action: str) -> list[AuditRecord]:
    session = sessionmaker(engine, class_=Session, expire_on_commit=False)()
    rows = session.execute(select(AuditRecord).where(AuditRecord.action == action)).scalars().all()
    session.close()
    return list(rows)


def make_service(client, token, name="checkout-service", tier="application"):
    resp = client.post(
        "/api/v1/services",
        headers=bearer(token),
        json={"name": name, "description": "checkout flows", "tier": tier},
    )
    assert resp.status_code == 201, resp.json()
    return resp.json()


def make_api(client, token, name="POST /orders", method="post", path="/orders"):
    resp = client.post(
        "/api/v1/apis",
        headers=bearer(token),
        json={"name": name, "method": method, "path": path, "description": "order intake"},
    )
    assert resp.status_code == 201, resp.json()
    return resp.json()


def test_catalog_writes_are_admin_only(client):
    token = login(client, VIEWER_EMAIL)
    resp = client.post("/api/v1/services", headers=bearer(token), json={"name": "svc"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "admin_required"
    resp = client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "OWNS", "srcId": TEAM_ID, "dstId": REPO_ID},
    )
    assert resp.status_code == 403
    resp = client.delete(
        "/api/v1/relationships?relType=OWNS&srcId=team&dstId=service", headers=bearer(token)
    )
    assert resp.status_code == 403


def test_catalog_reads_are_available_to_any_member(client):
    token = login(client, VIEWER_EMAIL)
    assert client.get("/api/v1/services", headers=bearer(token)).status_code == 200
    assert client.get("/api/v1/apis", headers=bearer(token)).status_code == 200
    assert client.get("/api/v1/relationships", headers=bearer(token)).status_code == 200


def test_service_create_stamps_manual_provenance_and_audits(client, engine):
    token = login(client, ADMIN_EMAIL)
    body = make_service(client, token)
    assert body["entityType"] == "Service"
    assert body["entityId"].startswith(f"enggraph:{ORG}:service:")
    assert body["name"] == "checkout-service"
    assert body["sourceSystem"] == "enggraph"
    assert body["isDemoData"] is False
    assert body["properties"]["organizationId"] == ORG
    assert body["properties"]["createdBy"]
    assert body["properties"]["tier"] == "application"
    rows = audits(engine, "catalog.service_create")
    assert len(rows) == 1
    assert rows[0].organization_id == ORG
    assert rows[0].target == body["entityId"]
    assert rows[0].details["name"] == "checkout-service"
    # The node is readable through the list endpoint, not just the write response.
    listing = client.get("/api/v1/services", headers=bearer(token)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["entityId"] == body["entityId"]


def test_service_create_rejects_an_empty_name(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.post("/api/v1/services", headers=bearer(token), json={"name": "   "})
    assert resp.status_code == 400


def test_service_patch_updates_only_supplied_fields(client, engine):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    resp = client.patch(
        f"/api/v1/services/{service['entityId']}",
        headers=bearer(token),
        json={"tier": "domain"},
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["properties"]["tier"] == "domain"
    # Untouched fields keep their values (patch is a merge, not a rewrite).
    assert body["name"] == "checkout-service"
    assert body["properties"]["description"] == "checkout flows"
    assert body["properties"]["updatedBy"]
    rows = audits(engine, "catalog.service_update")
    assert rows[0].target == service["entityId"]
    assert rows[0].details["tier"] == "domain"


def test_service_patch_rejects_an_empty_patch_and_non_services(client):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    empty = client.patch(f"/api/v1/services/{service['entityId']}", headers=bearer(token), json={})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "invalid_request"
    # A provider node is not a catalog service.
    wrong = client.patch(
        f"/api/v1/services/{REPO_ID}", headers=bearer(token), json={"tier": "domain"}
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "entity_not_service"
    missing = client.patch(
        "/api/v1/services/enggraph:acme:service:none",
        headers=bearer(token),
        json={"tier": "domain"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "entity_not_found"


def test_api_create_uppercases_the_method(client, engine):
    token = login(client, ADMIN_EMAIL)
    body = make_api(client, token)
    assert body["entityId"].startswith(f"enggraph:{ORG}:api:")
    assert body["entityType"] == "API"
    assert body["properties"]["method"] == "POST"
    assert body["properties"]["path"] == "/orders"
    assert body["sourceSystem"] == "enggraph"
    rows = audits(engine, "catalog.api_create")
    assert rows[0].details["method"] == "POST"


def test_relationship_create_records_manual_provenance(client, engine):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    resp = client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "owns", "srcId": TEAM_ID, "dstId": service["entityId"]},
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["subjectId"] == TEAM_ID
    assert body["relType"] == "OWNS"
    assert body["srcId"] == TEAM_ID
    assert body["dstId"] == service["entityId"]
    assert body["provenanceType"] == "MANUAL"
    assert body["sourceSystem"] == "enggraph"
    assert body["createdBy"]
    rows = audits(engine, "catalog.relationship_create")
    assert rows[0].target == f"{TEAM_ID}-[OWNS]->{service['entityId']}"
    assert rows[0].details["provenanceType"] == "MANUAL"
    # The manual edge is visible on the entity's relationship list.
    listed = client.get(f"/api/v1/entities/{TEAM_ID}/relationships", headers=bearer(token)).json()
    assert ("OWNS", service["entityId"]) in {(i["relType"], i["dstId"]) for i in listed["items"]}


def test_relationship_types_owned_by_sync_are_not_writable(client):
    """The manual allowlist is the write surface; provider types belong to sync."""
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={
            "relType": "CONTAINS_COMMIT",
            "srcId": REPO_ID,
            "dstId": "ado:acme:Commerce:commit:r-uuid-1:abc123",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "relationship_not_manual"


def test_relationship_endpoints_are_validated_by_type(client):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    # OWNS is Team -> Service; a Developer cannot own a Service here.
    resp = client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "OWNS", "srcId": DEVELOPER_ID, "dstId": service["entityId"]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_endpoints"


def test_relationship_endpoints_must_exist_in_the_callers_organization(client):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    missing = client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "OWNS", "srcId": "enggraph:acme:team:nope", "dstId": service["entityId"]},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "entity_not_found"
    # A node from another organization reads as missing.
    other = login(client, OTHER_ADMIN_EMAIL)
    cross = client.post(
        "/api/v1/relationships",
        headers=bearer(other),
        json={"relType": "DEPENDS_ON", "srcId": service["entityId"], "dstId": service["entityId"]},
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "entity_not_found"


def test_manual_relationships_can_be_deleted_and_are_audited(client, engine):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "OWNS", "srcId": TEAM_ID, "dstId": service["entityId"]},
    )
    resp = client.delete(
        f"/api/v1/relationships?relType=OWNS&srcId={TEAM_ID}&dstId={service['entityId']}",
        headers=bearer(token),
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["deleted"] is True
    listed = client.get("/api/v1/relationships", headers=bearer(token)).json()
    assert listed["total"] == 0
    rows = audits(engine, "catalog.relationship_delete")
    assert rows[0].target == f"{TEAM_ID}-[OWNS]->{service['entityId']}"


def test_provider_derived_relationships_are_not_deletable(client, seeded):
    """Sync owns provider edges; a manual delete attempt is refused, not silent."""
    _, repo = seeded
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    api = make_api(client, token)
    # A provider that declares service->API mapping writes EXPOSES with provider
    # provenance, through the same repository interface sync uses.
    repo.upsert_edges(
        [
            EdgeUpsert(
                rel_type="EXPOSES",
                src_label="Service",
                src_id=service["entityId"],
                dst_label="API",
                dst_id=api["entityId"],
                props={
                    "provenanceType": "PROVIDER_EXPLICIT",
                    "sourceSystem": "azure_devops",
                    "sourceRecordId": "provider:exposes",
                    "observedAt": "2026-01-01T00:00:00Z",
                },
                organization_id=ORG,
            )
        ]
    )
    resp = client.delete(
        f"/api/v1/relationships?relType=EXPOSES&srcId={service['entityId']}"
        f"&dstId={api['entityId']}",
        headers=bearer(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "relationship_provider_owned"
    # The edge is still there: refusal is not a partial write.
    listing = client.get("/api/v1/relationships", headers=bearer(token)).json()
    assert listing["total"] == 0, "provider edges are not manual, and not deletable"


def test_relationship_types_outside_the_allowlist_are_not_deletable(client):
    """CHANGES is written by sync only; the allowlist gates deletes as well."""
    token = login(client, ADMIN_EMAIL)
    resp = client.delete(
        f"/api/v1/relationships?relType=CHANGES&srcId={PR_ID}&dstId={COMMIT_ABC}",
        headers=bearer(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "relationship_not_manual"


def test_deleting_an_absent_relationship_is_404(client):
    token = login(client, ADMIN_EMAIL)
    service = make_service(client, token)
    resp = client.delete(
        f"/api/v1/relationships?relType=OWNS&srcId={TEAM_ID}&dstId={service['entityId']}",
        headers=bearer(token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "relationship_not_found"


def test_manual_relationship_list_is_scoped_and_ordered(client):
    token = login(client, ADMIN_EMAIL)
    first = make_service(client, token, name="svc-a")
    second = make_service(client, token, name="svc-b")
    api = make_api(client, token)
    client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "OWNS", "srcId": TEAM_ID, "dstId": first["entityId"]},
    )
    client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "EXPOSES", "srcId": first["entityId"], "dstId": api["entityId"]},
    )
    client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "DEPENDS_ON", "srcId": second["entityId"], "dstId": first["entityId"]},
    )
    listing = client.get("/api/v1/relationships", headers=bearer(token)).json()
    assert listing["total"] == 3
    keys = [(i["srcId"], i["relType"], i["dstId"]) for i in listing["items"]]
    assert keys == sorted(keys)
    assert all(i["provenanceType"] == "MANUAL" for i in listing["items"])
    # Provider-derived edges are not reported as manual relationships.
    assert not any(i["relType"] in ("CONTAINS_COMMIT", "HAS_MEMBER") for i in listing["items"])
    # Pagination: total stays stable across pages.
    page = client.get("/api/v1/relationships?limit=2&offset=2", headers=bearer(token)).json()
    assert page["total"] == 3
    assert len(page["items"]) == 1
    assert page["offset"] == 2


def test_catalog_reads_are_isolated_per_organization(client):
    token = login(client, ADMIN_EMAIL)
    make_service(client, token)
    other = login(client, OTHER_ADMIN_EMAIL)
    services = client.get("/api/v1/services", headers=bearer(other)).json()
    assert services["total"] == 0
    relationships = client.get("/api/v1/relationships", headers=bearer(other)).json()
    assert relationships["items"] == []


def test_catalog_reads_require_authentication(client):
    assert client.get("/api/v1/services").status_code == 401
    assert client.get("/api/v1/apis").status_code == 401
    assert client.get("/api/v1/relationships").status_code == 401
