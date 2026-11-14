"""End-to-end contract test: raw ADO payloads -> canonical graph rows.

The fetch layer runs against httpx.MockTransport fixtures, so normalization is
checked through the real ingestion path (pagination + caps + identity
fallbacks included), not hand-built rows.
"""

from __future__ import annotations

import httpx
import pytest
from ado_fixtures import OBSERVED, ORG, PAT, PROJECT, handler, load

from enggraph.connectors.azure_devops.client import AdoClient
from enggraph.connectors.azure_devops.connector import fetch_changes, fetch_project_graph
from enggraph.graph.model import EdgeUpsert, NodeUpsert

C = "ado:acme:Commerce"

IDS = {
    "org": "ado:azure:acme",
    "project": "ado:acme:project:p-uuid-1",
    "repo": f"{C}:repository:r-uuid-1",
    "team": "ado:acme:team:t-uuid-1",
    "dana": "ado:acme:identity:i-uuid-1",
    "omar": "ado:acme:identity:i-uuid-2",
    "dana_commit": "ado:acme:identity:u.dana@acme.com",
    "omar_commit": "ado:acme:identity:u.omar@acme.com",
    "pr": f"{C}:pullrequest:r-uuid-1:7",
    "abc": f"{C}:commit:r-uuid-1:abc123",
    "def456": f"{C}:commit:r-uuid-1:def456",
    "fff": f"{C}:commit:r-uuid-1:fff777",
    "wi42": f"{C}:workitem:42",
    "wi43": f"{C}:workitem:43",
}

EXPECTED_EDGES = {
    ("HAS_PROJECT", IDS["org"], IDS["project"]),
    ("CONTAINS_REPOSITORY", IDS["project"], IDS["repo"]),
    ("HAS_TEAM", IDS["org"], IDS["team"]),
    ("HAS_MEMBER", IDS["team"], IDS["dana"]),
    ("CONTAINS_COMMIT", IDS["repo"], IDS["abc"]),
    ("CONTAINS_COMMIT", IDS["repo"], IDS["def456"]),
    ("CONTAINS_COMMIT", IDS["repo"], IDS["fff"]),
    ("AUTHORED", IDS["dana_commit"], IDS["abc"]),
    ("AUTHORED", IDS["omar_commit"], IDS["def456"]),
    ("AUTHORED", IDS["dana_commit"], IDS["fff"]),
    ("CONTAINS_PULL_REQUEST", IDS["repo"], IDS["pr"]),
    ("CREATED_BY", IDS["pr"], IDS["dana"]),
    ("REVIEWED_BY", IDS["pr"], IDS["omar"]),
    ("CHANGES", IDS["pr"], IDS["abc"]),
    ("LINKED_TO", IDS["pr"], IDS["wi42"]),
    ("LINKED_TO", IDS["pr"], IDS["wi43"]),
    ("PARENT_OF", IDS["wi42"], IDS["wi43"]),
}


@pytest.fixture(scope="module")
def graph() -> tuple[list[NodeUpsert], list[EdgeUpsert]]:
    project_raw = load("projects_p1")["value"][0]
    client = AdoClient(ORG, PAT, page_size=2, transport=httpx.MockTransport(handler))
    with client:
        return fetch_project_graph(client, ORG, project_raw, OBSERVED)


def node_map(nodes: list[NodeUpsert]) -> dict[str, NodeUpsert]:
    return {n.id: n for n in nodes}


def test_canonical_ids_are_used(graph):
    ids = set(node_map(graph[0]))
    for key in (
        "org",
        "project",
        "repo",
        "team",
        "dana",
        "omar",
        "dana_commit",
        "omar_commit",
        "pr",
        "abc",
        "def456",
        "fff",
        "wi42",
        "wi43",
    ):
        assert IDS[key] in ids, f"missing canonical node {IDS[key]}"


def test_edge_shape_is_exact(graph):
    nodes, edges = graph
    seen = {(e.rel_type, e.src_id, e.dst_id) for e in edges}
    assert seen == EXPECTED_EDGES
    assert len(edges) == len(EXPECTED_EDGES)  # no duplicate emissions
    assert len(nodes) == len(node_map(nodes))  # node dedupe by canonical id


def test_edges_never_reference_missing_nodes(graph):
    nodes, edges = graph
    ids = set(node_map(nodes))
    for e in edges:
        assert e.src_id in ids and e.dst_id in ids
    # work item 99 is referenced by a PR link on page 2 but never fetched
    assert "ado:acme:Commerce:workitem:99" not in ids
    assert all("workitem:99" not in (e.src_id, e.dst_id) for e in edges)


def test_every_edge_is_domain_valid(graph):
    for e in graph[1]:
        e.validate_endpoints()


def test_provider_edges_carry_provenance(graph):
    for e in graph[1]:
        assert e.props["provenanceType"] == "PROVIDER_EXPLICIT"
        assert e.props["sourceSystem"] == "azure_devops"
        assert e.props["observedAt"] == OBSERVED
        assert e.props["sourceRecordId"]
        assert e.organization_id == ORG


def test_source_ids_and_urls_are_preserved(graph):
    by_id = node_map(graph[0])
    project = by_id[IDS["project"]].sanitized_props()
    assert project["sourceId"] == "p-uuid-1"
    assert project["sourceUrl"] == "https://dev.azure.com/acme/Commerce"
    repo = by_id[IDS["repo"]].sanitized_props()
    assert repo["defaultBranch"] == "refs/heads/main"
    assert repo["sourceUrl"].endswith("repositories/r-uuid-1")
    workitem = by_id[IDS["wi42"]].sanitized_props()
    assert workitem["title"] == "Add caching"
    assert workitem["state"] == "Resolved"
    assert workitem["priority"] == "High"
    assert workitem["sourceId"] == "42"


def test_null_provider_fields_are_dropped(graph):
    by_id = node_map(graph[0])
    props = by_id[IDS["wi43"]].sanitized_props()
    assert props["title"] == "Metrics gap"
    for missing in ("priority", "severity", "reason", "createdDate", "assignedToLabel"):
        assert missing not in props, f"null prop {missing} survived sanitization"


def test_commit_only_identity_falls_back_to_unique_name(graph):
    by_id = node_map(graph[0])
    dana = by_id[IDS["dana_commit"]].sanitized_props()
    assert dana["displayName"] == "Dana Diaz"
    assert dana["uniqueName"] == "dana@acme.com"
    assert "sourceId" not in dana  # identity block gave no stable id
    # team member with a stable id is a distinct canonical node
    assert by_id[IDS["dana"]].sanitized_props()["sourceId"] == "i-uuid-1"


def test_second_pages_reach_the_graph(graph):
    ids = set(node_map(graph[0]))
    assert IDS["fff"] in ids  # page 2 of commits
    assert IDS["wi43"] in ids  # page 2 of PR work-item links -> link edge exists
    edges = {(e.rel_type, e.src_id, e.dst_id) for e in graph[1]}
    assert ("LINKED_TO", IDS["pr"], IDS["wi43"]) in edges
    assert ("PARENT_OF", IDS["wi42"], IDS["wi43"]) in edges


def test_wiql_bulk_ids_are_sent_as_one_bulk_read():
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = AdoClient(ORG, PAT, page_size=2, transport=httpx.MockTransport(recording))
    project_raw = load("projects_p1")["value"][0]
    with client:
        fetch_project_graph(client, ORG, project_raw, OBSERVED)
    get_paths = [r.url.path.removeprefix("/acme") for r in seen if r.method == "GET"]
    workitem_requests = [p for p in get_paths if p.endswith("/workitems")]
    assert len(workitem_requests) == 1  # 2 ids fit one chunk of 100


def test_changes_api_contract_for_incremental_sync():
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = AdoClient(ORG, PAT, page_size=2, transport=httpx.MockTransport(recording))
    body = fetch_changes(client, PROJECT, 5)
    request = seen[0]
    params = dict(request.url.params)
    # Changes API is project-scoped: the project must appear in the path.
    assert request.url.path.removeprefix("/acme") == f"/{PROJECT}/_apis/wit/changes"
    assert params["version"] == "5"
    assert params["top"] == "2"
    assert params["api-version"] == "7.2"
    assert len(body["changes"]) == 2
    assert body["nextVersion"] == 6
