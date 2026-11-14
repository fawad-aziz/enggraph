"""Change-impact API tests (FR-09).

Impact analysis is bounded and explainable: every finding carries the
relationship path that produced it, the report names the bounds in effect, the
result set is labelled truncated when a bound was consumed, and reports are
persisted organization-scoped. Inferred findings come from the single documented
cross-consumer rule with fixed confidence.
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
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.persistence.models import AuditRecord, ImpactAnalysis

PR_ID = "ado:acme:Commerce:pullrequest:r-uuid-1:7"
WI_42 = "ado:acme:Commerce:workitem:42"
COMMIT_ABC = "ado:acme:Commerce:commit:r-uuid-1:abc123"
COMMIT_DEF = "ado:acme:Commerce:commit:r-uuid-1:def456"
COMMIT_FFF = "ado:acme:Commerce:commit:r-uuid-1:fff777"


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


def analyze(client, token, body: dict) -> dict:
    resp = client.post("/api/v1/impact-analysis", headers=bearer(token), json=body)
    assert resp.status_code == 201, resp.json()
    return resp.json()


def find_by_id(report: dict, entity_id: str) -> dict:
    matches = [f for f in report["findings"] if f["entityId"] == entity_id]
    assert matches, f"{entity_id} is not a finding"
    return matches[0]


def assert_path_is_a_witness(finding: dict) -> None:
    """Every finding must be explainable edge by edge, not just asserted.

    The entity list and the relationship list must form a connected chain from
    the analysed subject to the finding's entity, with each edge joining the two
    entities it names. BFS ordering is not pinned; connectivity is.
    """
    entities = [e["entityId"] for e in finding["path"]["entities"]]
    rels = finding["path"]["relationships"]
    assert len(entities) == len(rels) + 1
    for i, rel in enumerate(rels):
        a, b = entities[i], entities[i + 1]
        assert {rel["srcId"], rel["dstId"]} == {a, b}, (
            f"path edge {rel['relType']} does not join {a} and {b}"
        )
    assert entities[-1] == finding["entityId"]
    assert finding["depth"] == len(rels)


def test_impact_analysis_requires_authentication(client):
    resp = client.post(
        "/api/v1/impact-analysis", json={"subjectId": REPO_ID, "categories": ["DIRECT"]}
    )
    assert resp.status_code == 401


def test_viewer_can_run_analysis(client):
    """Impact analysis is a read operation: no admin role required."""
    token = login(client, VIEWER_EMAIL)
    report = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DIRECT"]})
    assert report["subjectId"] == REPO_ID
    assert report["subjectType"] == "Repository"
    assert report["subjectName"] == "commerce-api"


def test_direct_findings_explain_their_paths(client):
    token = login(client, ADMIN_EMAIL)
    report = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DIRECT"]})
    # Every node in the seeded organization is reachable from the repository.
    assert report["counts"]["DIRECT"] == 13
    assert report["truncated"] is False
    # Findings are ordered by category, depth, then entity id.
    first = report["findings"][0]
    assert first["depth"] == 1
    assert first["entityId"] == COMMIT_ABC
    assert first["category"] == "DIRECT"
    assert first["confidence"] == 1.0
    assert first["provenanceTypes"] == ["PROVIDER_EXPLICIT"]
    assert first["hasManualEvidence"] is False
    assert [e["entityId"] for e in first["path"]["entities"]] == [REPO_ID, COMMIT_ABC]
    assert [r["relType"] for r in first["path"]["relationships"]] == ["CONTAINS_COMMIT"]
    depth_one = {f["entityId"] for f in report["findings"] if f["depth"] == 1}
    assert depth_one == {COMMIT_ABC, COMMIT_DEF, COMMIT_FFF, PR_ID, PROJECT_ID}
    # Every finding's path is a connected witness, edge by edge.
    for finding in report["findings"]:
        assert_path_is_a_witness(finding)
    # The team is three hops away (member route: repository -> PR -> member -> team).
    team = find_by_id(report, TEAM_ID)
    assert team["depth"] == 3
    assert [r["relType"] for r in team["path"]["relationships"]][-1] == "HAS_MEMBER"


def test_a_finding_is_only_listed_because_a_matching_path_exists(client):
    """DEPENDENCY findings require DEPENDS_ON/CONSUMES/EXPOSES/IMPLEMENTED_BY."""
    token = login(client, ADMIN_EMAIL)
    report = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DEPENDENCY"]})
    assert report["counts"]["DEPENDENCY"] == 0
    assert report["findings"] == []
    # HISTORICAL traversal follows commit/PR/work-item relationships only.
    historical = analyze(client, token, {"subjectId": REPO_ID, "categories": ["HISTORICAL"]})
    assert historical["counts"]["HISTORICAL"] == 8
    work_item = find_by_id(historical, WI_42)
    assert work_item["depth"] == 2
    assert [r["relType"] for r in work_item["path"]["relationships"]] == [
        "CONTAINS_PULL_REQUEST",
        "LINKED_TO",
    ]


def test_report_always_carries_a_limitations_section(client):
    token = login(client, ADMIN_EMAIL)
    report = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DIRECT"]})
    text = " ".join(report["limitations"])
    assert "depth 4" in text
    assert "not a complete impact census" in text
    assert "last successful synchronization" in text
    assert "unconfigured, not absent" in text


def test_bounds_are_clamped_to_the_configuration(client, settings):
    token = login(client, ADMIN_EMAIL)
    report = analyze(
        client,
        token,
        {
            "subjectId": REPO_ID,
            "categories": ["DIRECT"],
            "depth": 10,
            "limit": 1000,
        },
    )
    assert report["depth"] == settings.max_depth
    assert report["limit"] == settings.max_results
    # Zero is rejected before any traversal, not clamped upward.
    resp = client.post(
        "/api/v1/impact-analysis",
        headers=bearer(token),
        json={"subjectId": REPO_ID, "categories": ["DIRECT"], "depth": 0},
    )
    assert resp.status_code == 400


def test_truncation_is_reported_when_a_bound_is_consumed(client):
    """A bounded report must say it is bounded; otherwise it reads as a census."""
    token = login(client, ADMIN_EMAIL)
    report = analyze(
        client,
        token,
        {
            "subjectId": REPO_ID,
            "categories": ["DIRECT"],
            "depth": 1,
            "limit": 2,
        },
    )
    assert len(report["findings"]) == 2
    assert report["truncated"] is True
    assert any("truncated" in line for line in report["limitations"])
    # The same subject with the full budget is not truncated.
    full = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DIRECT"]})
    assert full["truncated"] is False


def test_unknown_category_is_rejected(client):
    token = login(client, ADMIN_EMAIL)
    resp = client.post(
        "/api/v1/impact-analysis",
        headers=bearer(token),
        json={"subjectId": REPO_ID, "categories": ["WATERSHED"]},
    )
    assert resp.status_code == 400


def test_subject_must_be_readable_by_the_caller(client):
    token = login(client, ADMIN_EMAIL)
    missing = client.post(
        "/api/v1/impact-analysis",
        headers=bearer(token),
        json={"subjectId": "ado:acme:project:nope", "categories": ["DIRECT"]},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "entity_not_found"
    other = login(client, OTHER_ADMIN_EMAIL)
    cross = client.post(
        "/api/v1/impact-analysis",
        headers=bearer(other),
        json={"subjectId": REPO_ID, "categories": ["DIRECT"]},
    )
    # Same 404 as a missing entity: no cross-tenant enumeration signal.
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "entity_not_found"


def test_inferred_findings_come_from_the_documented_rule(client):
    """Services sharing one API are cross-consumers, at fixed confidence."""
    token = login(client, ADMIN_EMAIL)
    provider = client.post(
        "/api/v1/services", headers=bearer(token), json={"name": "orders-service"}
    ).json()
    consumer = client.post(
        "/api/v1/services", headers=bearer(token), json={"name": "inventory-service"}
    ).json()
    api = client.post(
        "/api/v1/apis",
        headers=bearer(token),
        json={"name": "POST /orders", "method": "post", "path": "/orders"},
    ).json()
    client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "EXPOSES", "srcId": provider["entityId"], "dstId": api["entityId"]},
    )
    client.post(
        "/api/v1/relationships",
        headers=bearer(token),
        json={"relType": "CONSUMES", "srcId": consumer["entityId"], "dstId": api["entityId"]},
    )

    report = analyze(client, token, {"subjectId": provider["entityId"], "categories": ["INFERRED"]})
    assert report["counts"]["INFERRED"] == 1
    inferred = report["findings"][0]
    assert inferred["category"] == "INFERRED"
    assert inferred["entityId"] == consumer["entityId"]
    assert inferred["confidence"] == 0.6
    assert inferred["depth"] == 2
    assert inferred["provenanceTypes"] == ["MANUAL"]
    assert inferred["hasManualEvidence"] is True
    assert [e["entityId"] for e in inferred["path"]["entities"]] == [
        provider["entityId"],
        api["entityId"],
        consumer["entityId"],
    ]
    assert_path_is_a_witness(inferred)
    # The inference rule is named in the limitations, next to its confidence.
    text = " ".join(report["limitations"])
    assert "services sharing an API" in text
    assert "0.6" in text


def test_reports_are_persisted_and_audited(client, engine):
    token = login(client, ADMIN_EMAIL)
    report = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DIRECT"]})
    session = sessionmaker(engine, class_=Session, expire_on_commit=False)()
    row = session.get(ImpactAnalysis, report["analysisId"])
    assert row is not None
    assert row.organization_id == ORG
    assert row.subject_id == REPO_ID
    assert row.depth == 4
    assert row.limit == 200
    assert row.result_count == len(report["findings"])
    audit = (
        session.execute(select(AuditRecord).where(AuditRecord.action == "impact.analysis"))
        .scalars()
        .all()
    )
    assert audit[0].target == report["analysisId"]
    assert audit[0].organization_id == ORG
    session.close()
    # The persisted report is re-readable by id.
    fetched = client.get(
        f"/api/v1/impact-analysis/{report['analysisId']}", headers=bearer(token)
    ).json()
    assert fetched["analysisId"] == report["analysisId"]
    assert len(fetched["findings"]) == len(report["findings"])
    assert fetched["limitations"] == report["limitations"]


def test_reports_are_not_readable_across_organizations(client):
    token = login(client, ADMIN_EMAIL)
    report = analyze(client, token, {"subjectId": REPO_ID, "categories": ["DIRECT"]})
    other = login(client, OTHER_ADMIN_EMAIL)
    resp = client.get(f"/api/v1/impact-analysis/{report['analysisId']}", headers=bearer(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "analysis_not_found"
    unknown = client.get("/api/v1/impact-analysis/no-such-id", headers=bearer(token))
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "analysis_not_found"
