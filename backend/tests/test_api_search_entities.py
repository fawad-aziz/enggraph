"""Search + entity read API tests (FR-05, FR-08).

Everything here reads the graph produced by the real sync worker against the
recorded fixtures. Assertions cover organization scoping, pagination totals,
type validation, depth/limit clamping, and the `truncated` signal that tells a
caller a bound was hit (so the UI never implies a complete census).
"""

from __future__ import annotations

import pytest
from ado_fixtures import ORG
from api_support import (
    ADMIN_EMAIL,
    OTHER_ADMIN_EMAIL,
    OTHER_ORG,
    PROJECT_ID,
    REPO_ID,
    TEAM_ID,
    VIEWER_EMAIL,
    add_user,
    bearer,
    build_app,
    login,
)
from fastapi.testclient import TestClient

PR_ID = "ado:acme:Commerce:pullrequest:r-uuid-1:7"
WI_42 = "ado:acme:Commerce:workitem:42"
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


def test_search_requires_authentication(client):
    resp = client.get("/api/v1/search?q=commerce")
    assert resp.status_code == 401


def test_search_finds_ingested_provider_data(client):
    token = login(client, VIEWER_EMAIL)
    resp = client.get("/api/v1/search?q=commerce", headers=bearer(token))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["query"] == "commerce"
    assert body["total"] == 2
    ids = [i["entityId"] for i in body["items"]]
    assert sorted(ids) == sorted([PROJECT_ID, REPO_ID])
    assert body["types"] == [
        "Organization",
        "Project",
        "Team",
        "Developer",
        "Repository",
        "WorkItem",
        "PullRequest",
        "Commit",
        "Service",
        "API",
    ]


def test_search_type_filter_narrows_labels(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get("/api/v1/search?q=commerce&types=Repository", headers=bearer(token)).json()
    assert body["total"] == 1
    assert body["items"][0]["entityId"] == REPO_ID
    assert body["items"][0]["entityType"] == "Repository"


def test_search_reports_dto_fields_used_by_the_ui(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get("/api/v1/search?q=caching&types=WorkItem", headers=bearer(token)).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["entityId"] == WI_42
    assert item["name"] == "Add caching"
    assert item["sourceSystem"] == "azure_devops"
    assert item["sourceUrl"]
    assert item["isDemoData"] is False


def test_unknown_entity_type_is_a_bounded_request_error(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.get("/api/v1/search?q=commerce&types=Widget", headers=bearer(token))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_entity_type"


def test_overlong_query_is_rejected_before_any_scan(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.get("/api/v1/search?q=" + "a" * 121, headers=bearer(token))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_search_pagination_totals_stay_stable(client):
    token = login(client, ADMIN_EMAIL)
    page1 = client.get("/api/v1/search?q=commerce&limit=1", headers=bearer(token)).json()
    page2 = client.get("/api/v1/search?q=commerce&limit=1&offset=1", headers=bearer(token)).json()
    assert page1["total"] == page2["total"] == 2
    assert len(page1["items"]) == 1 and len(page2["items"]) == 1
    assert page1["items"][0]["entityId"] != page2["items"][0]["entityId"]
    # Sorted by (label, id): Project precedes Repository.
    assert page1["items"][0]["entityId"] == PROJECT_ID


def test_search_is_isolated_per_organization(client):
    """The principal's organization is the only readable scope."""
    other = login(client, OTHER_ADMIN_EMAIL)
    body = client.get("/api/v1/search?q=commerce", headers=bearer(other)).json()
    assert body["total"] == 0
    assert body["items"] == []


def test_entity_detail_returns_the_stored_node(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(f"/api/v1/entities/{REPO_ID}", headers=bearer(token)).json()
    assert body["entityId"] == REPO_ID
    assert body["entityType"] == "Repository"
    assert body["name"] == "commerce-api"
    assert body["properties"]["sourceId"] == "r-uuid-1"
    assert body["properties"]["organizationId"] == ORG


def test_entity_detail_is_404_for_unknown_and_cross_org_ids(client):
    token = login(client, ADMIN_EMAIL)
    missing = client.get("/api/v1/entities/ado:acme:project:nope", headers=bearer(token))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "entity_not_found"
    other = login(client, OTHER_ADMIN_EMAIL)
    cross = client.get(f"/api/v1/entities/{PROJECT_ID}", headers=bearer(other))
    # Same 404 as a missing entity: no cross-tenant enumeration signal.
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "entity_not_found"


def test_relationships_report_both_directions(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(f"/api/v1/entities/{PR_ID}/relationships", headers=bearer(token)).json()
    assert body["subjectId"] == PR_ID
    outgoing = {(i["relType"], i["dstId"]) for i in body["items"] if i["direction"] == "outgoing"}
    incoming = {(i["relType"], i["srcId"]) for i in body["items"] if i["direction"] == "incoming"}
    assert ("CHANGES", COMMIT_ABC) in outgoing
    assert ("LINKED_TO", WI_42) in outgoing
    assert ("CONTAINS_PULL_REQUEST", REPO_ID) in incoming
    assert body["truncated"] is False


def test_relationship_limit_reports_truncation(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(
        f"/api/v1/entities/{REPO_ID}/relationships?limit=1", headers=bearer(token)
    ).json()
    # Outgoing and incoming are each capped at the limit, and the bound is reported.
    assert body["limit"] == 1
    assert body["truncated"] is True


def test_neighborhood_is_depth_bounded_and_reports_truncation(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(
        f"/api/v1/entities/{REPO_ID}/neighborhood?depth=1&limit=2", headers=bearer(token)
    ).json()
    assert body["depth"] == 1
    assert body["limit"] == 2
    assert len(body["nodes"]) == 2
    assert body["truncated"] is True
    assert body["subject"]["entityId"] == REPO_ID


def test_neighborhood_filters_are_validated_and_applied(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(
        f"/api/v1/entities/{REPO_ID}/neighborhood?depth=1&limit=50"
        "&node_types=Commit&rel_types=CONTAINS_COMMIT",
        headers=bearer(token),
    ).json()
    labels = {n["entityType"] for n in body["nodes"]}
    assert labels == {"Repository", "Commit"}
    assert len(body["nodes"]) == 4
    assert {e["relType"] for e in body["edges"]} == {"CONTAINS_COMMIT"}
    assert body["filters"] == {"nodeTypes": ["Commit"], "relTypes": ["CONTAINS_COMMIT"]}


def test_neighborhood_rejects_unknown_filter_values(client):
    token = login(client, ADMIN_EMAIL)
    bad_label = client.get(
        f"/api/v1/entities/{REPO_ID}/neighborhood?node_types=Widget", headers=bearer(token)
    )
    assert bad_label.status_code == 400
    assert bad_label.json()["error"]["code"] == "invalid_entity_type"
    bad_rel = client.get(
        f"/api/v1/entities/{REPO_ID}/neighborhood?rel_types=MUTATES", headers=bearer(token)
    )
    assert bad_rel.status_code == 400
    assert bad_rel.json()["error"]["code"] == "invalid_relationship_type"


def test_neighborhood_depth_is_clamped_to_the_configuration(client, settings):
    token = login(client, ADMIN_EMAIL)
    body = client.get(
        f"/api/v1/entities/{REPO_ID}/neighborhood?depth=10&limit=1000", headers=bearer(token)
    ).json()
    assert body["depth"] == settings.max_depth
    assert body["limit"] == settings.max_results
    # depth 10 out of range is rejected, not clamped upward.
    resp = client.get(f"/api/v1/entities/{REPO_ID}/neighborhood?depth=11", headers=bearer(token))
    assert resp.status_code == 400


def test_paths_explain_the_route_between_two_entities(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(
        f"/api/v1/entities/{REPO_ID}/paths?to={WI_42}&depth=2&limit=5", headers=bearer(token)
    ).json()
    assert body["from"] == REPO_ID
    assert body["to"] == WI_42
    assert body["paths"], "the seeded graph contains a repository -> PR -> work item route"
    route = [e["entityId"] for e in body["paths"][0]["entities"]]
    assert route == [REPO_ID, PR_ID, WI_42]
    rels = [r["relType"] for r in body["paths"][0]["relationships"]]
    assert rels == ["CONTAINS_PULL_REQUEST", "LINKED_TO"]


def test_paths_stop_at_the_depth_bound(client):
    token = login(client, ADMIN_EMAIL)
    body = client.get(
        f"/api/v1/entities/{REPO_ID}/paths?to={TEAM_ID}&depth=2&limit=5", headers=bearer(token)
    ).json()
    assert body["paths"] == []


def test_paths_require_both_endpoints_to_be_readable(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.get(
        f"/api/v1/entities/{REPO_ID}/paths?to=ado:acme:project:nope", headers=bearer(token)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "entity_not_found"


def test_graph_read_reports_unavailable_store(settings, engine, fake_driver):
    """No graph store is a 503, not a crash and not an empty result."""
    app, _ = build_app(settings, engine, fake_driver, seed=False)
    add_user(engine, ADMIN_EMAIL, "admin", ORG)
    with TestClient(app, raise_server_exceptions=False) as client:
        # The app context is built at startup; disconnect the graph store.
        client.app.state.ctx.repo = None
        token = login(client, ADMIN_EMAIL)
        resp = client.get("/api/v1/search?q=commerce", headers=bearer(token))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "graph_unavailable"
