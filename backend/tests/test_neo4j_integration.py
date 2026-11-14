"""Graph integration tests against a real Neo4j instance.

Gate: runs only when ENGGRAPH_NEO4J_TEST_URI points to a reachable Neo4j
(e.g. `docker compose up neo4j` with NEO4J_PASSWORD in .env). When the
variable is absent, tests SKIP with an explicit message — never silently.

Example:
  ENGGRAPH_NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_PASSWORD=... \
    python -m pytest -m neo4j
"""

from __future__ import annotations

import os

import pytest
from neo4j import GraphDatabase

from enggraph.graph.model import NODE_LABELS, EdgeUpsert, NodeUpsert
from enggraph.graph.neo4j_repo import Neo4jGraphRepository
from enggraph.graph.schema import install_schema

pytestmark = pytest.mark.neo4j

ORG = "itest-org"
R = "ado:itest:proj1:repo1"
PR = "ado:itest:proj1:repo1:pr1"
C = "ado:itest:proj1:repo1:c1"
SVC = "enggraph:itest:svc1"
API = "enggraph:itest:api1"


@pytest.fixture(scope="module")
def neo4j_repo():
    uri = os.environ.get("ENGGRAPH_NEO4J_TEST_URI")
    if not uri:
        pytest.skip(
            "ENGGRAPH_NEO4J_TEST_URI not set — real Neo4j required "
            "(start with `docker compose up neo4j`; see docs/development.md)"
        )
    password = os.environ.get("NEO4J_PASSWORD", "")
    user = os.environ.get("NEO4J_USER", "neo4j")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    driver.execute_query("MATCH (n) DETACH DELETE n", database_="neo4j")
    install_schema(driver)
    repo = Neo4jGraphRepository(driver)
    yield repo
    driver.close()


def n(label: str, id: str, **props) -> NodeUpsert:
    props.setdefault("organizationId", ORG)
    return NodeUpsert(label=label, id=id, props=props, organization_id=ORG)


def e(rel: str, src_label: str, src: str, dst_label: str, dst: str, **props) -> EdgeUpsert:
    props.setdefault("provenanceType", "PROVIDER_EXPLICIT")
    props.setdefault("observedAt", "2026-01-01T00:00:00Z")
    return EdgeUpsert(
        rel_type=rel, src_label=src_label, src_id=src, dst_label=dst_label,
        dst_id=dst, props=props, organization_id=ORG,
    )


def fixture_rows() -> tuple[list, list]:
    nodes = [
        n("Organization", "ado:azure:itest", name="ITest", provider="azure_devops"),
        n("Project", "ado:itest:proj1", name="Alpha"),
        n("Team", "ado:itest:team1", name="Core"),
        n("Developer", "ado:itest:dev1", displayName="Dev One"),
        n("Repository", "ado:itest:proj1:repo1", name="alpha-service"),
        n("WorkItem", "ado:itest:proj1:wi1", title="Add caching", state="New"),
        n("PullRequest", "ado:itest:proj1:repo1:pr1", title="Cache layer", status="completed"),
        n("Commit", "ado:itest:proj1:repo1:c1", hash="feedface", message="cache"),
        n("Service", "enggraph:itest:svc1", name="Alpha Service"),
        n("API", "enggraph:itest:api1", name="Alpha API"),
        n("Project", "ado:otherorg:projX", name="Foreign", organizationId="otherorg"),
        n("Repository", "ado:otherorg:projX:repoX", name="foreign-repo", organizationId="otherorg"),
    ]
    manual = {"provenanceType": "MANUAL", "createdBy": "tester"}
    edges = [
        e("HAS_PROJECT", "Organization", "ado:azure:itest", "Project", "ado:itest:proj1"),
        e("HAS_TEAM", "Organization", "ado:azure:itest", "Team", "ado:itest:team1"),
        e("HAS_MEMBER", "Team", "ado:itest:team1", "Developer", "ado:itest:dev1"),
        e("CONTAINS_REPOSITORY", "Project", "ado:itest:proj1", "Repository", R),
        e("CONTAINS_PULL_REQUEST", "Repository", R, "PullRequest", PR),
        e("CONTAINS_COMMIT", "Repository", R, "Commit", C),
        e("AUTHORED", "Developer", "ado:itest:dev1", "Commit", C),
        e("CREATED_BY", "PullRequest", PR, "Developer", "ado:itest:dev1"),
        e("LINKED_TO", "PullRequest", PR, "WorkItem", "ado:itest:proj1:wi1",
          sourceRecordId="evi-1"),
        e("CHANGES", "PullRequest", PR, "Commit", C),
        e("OWNS", "Team", "ado:itest:team1", "Service", SVC, **manual),
        e("IMPLEMENTED_BY", "Service", SVC, "Repository", R, **manual),
        e("EXPOSES", "Service", SVC, "API", "enggraph:itest:api1", **manual),
    ]
    return nodes, edges


def test_schema_install_is_repeatable(neo4j_repo):
    # installing twice must not fail
    install_schema(neo4j_repo.driver)
    constraints = neo4j_repo.driver.execute_query(
        "SHOW CONSTRAINTS YIELD name, labelsOrProperties, type", database_="neo4j"
    ).records
    unique_id_labels = {
        entry.split(":")[0]
        for r in constraints
        if r["type"] == "UNIQUE"
        for entry in r["labelsOrProperties"]
    }
    for label in NODE_LABELS:
        assert label in unique_id_labels, f"no uniqueness constraint for {label}"


def test_upsert_idempotency(neo4j_repo):
    nodes, edges = fixture_rows()
    neo4j_repo.upsert_nodes(nodes)
    neo4j_repo.upsert_edges(edges)
    counts1 = neo4j_repo.counts(ORG)
    neo4j_repo.upsert_nodes(nodes)  # full sync rerun
    neo4j_repo.upsert_edges(edges)
    counts2 = neo4j_repo.counts(ORG)
    assert counts1 == counts2
    assert counts1["WorkItem"] == 1
    linked = neo4j_repo.outgoing("ado:itest:proj1:repo1:pr1", ORG, ["LINKED_TO"], 10)
    assert len(linked) == 1  # MERGE, no duplicate edge


def test_relationship_direction_and_provenance(neo4j_repo):
    linked = neo4j_repo.outgoing("ado:itest:proj1:repo1:pr1", ORG, ["LINKED_TO"], 10)
    assert [(x.rel_type, x.dst) for x in linked] == [("LINKED_TO", "ado:itest:proj1:wi1")]
    inbound = neo4j_repo.incoming("ado:itest:proj1:wi1", ORG, ["LINKED_TO"], 10)
    assert [(x.rel_type, x.src) for x in inbound] == [("LINKED_TO", "ado:itest:proj1:repo1:pr1")]
    assert linked[0].props["provenanceType"] == "PROVIDER_EXPLICIT"
    assert linked[0].props["sourceRecordId"] == "evi-1"
    owns = neo4j_repo.incoming("enggraph:itest:svc1", ORG, ["OWNS"], 10)
    assert owns[0].props["provenanceType"] == "MANUAL"
    assert owns[0].props["createdBy"] == "tester"


def test_org_isolation(neo4j_repo):
    hits = neo4j_repo.search("foreign", ["Project", "Repository"], ORG, 10)
    assert hits == []
    assert neo4j_repo.get_node("ado:otherorg:projX", ORG) is None
    nb = neo4j_repo.neighborhood("ado:itest:proj1:repo1", ORG, [], [], 2, 100)
    assert all(node.props.get("organizationId") == ORG for node in nb.nodes)


def test_bounded_neighborhood(neo4j_repo):
    nb1 = neo4j_repo.neighborhood("ado:itest:proj1", ORG, [], ["CONTAINS_REPOSITORY"], 1, 100)
    ids = {node.id for node in nb1.nodes}
    assert ids == {"ado:itest:proj1", "ado:itest:proj1:repo1"}
    rels2 = ["CONTAINS_REPOSITORY", "CONTAINS_PULL_REQUEST", "LINKED_TO"]
    nb2 = neo4j_repo.neighborhood("ado:itest:proj1", ORG, [], rels2, 2, 100)
    assert "ado:itest:proj1:repo1:pr1" in {node.id for node in nb2.nodes}
    assert "ado:itest:proj1:wi1" in {node.id for node in nb2.nodes}
    # depth 1 must not reach PR (2 hops away)
    nb1b = neo4j_repo.neighborhood("ado:itest:proj1", ORG, [], [], 1, 100)
    assert "ado:itest:proj1:repo1:pr1" not in {node.id for node in nb1b.nodes}


def test_paths_explain_results(neo4j_repo):
    # add the CONSUMES edge so svc2 -> api1 <- svc1 forms a 2-edge path
    neo4j_repo.upsert_nodes([n("Service", "enggraph:itest:svc2", name="Beta Service")])
    neo4j_repo.upsert_edges([
        e("CONSUMES", "Service", "enggraph:itest:svc2", "API", "enggraph:itest:api1",
          provenanceType="MANUAL", createdBy="tester"),
    ])
    paths = neo4j_repo.find_paths("enggraph:itest:svc2", "enggraph:itest:svc1", ORG,
                                  ["CONSUMES", "EXPOSES"], 2, 10)
    assert paths
    p = paths[0]
    assert p.nodes[0].id == "enggraph:itest:svc2"
    assert p.nodes[-1].id == "enggraph:itest:svc1"
    assert len(p.edges) == 2
    # bounded depth: depth 1 must not reach a 2-edge target
    assert neo4j_repo.find_paths("enggraph:itest:svc2", "enggraph:itest:svc1", ORG,
                                 ["CONSUMES", "EXPOSES"], 1, 10) == []


def test_deactivation_and_demo_reset(neo4j_repo):
    neo4j_repo.upsert_nodes([
        n("WorkItem", "ado:itest:proj1:wi-stale", title="Stale item",
          lastSeenAt="2026-01-01T00:00:00Z"),
        n("Service", "enggraph:itest:svc-demo", name="Demo service",
          demoData=True, lastSeenAt="2026-11-01T00:00:00Z"),
    ])
    stale = neo4j_repo.mark_not_seen(ORG, "2026-06-01T00:00:00Z", ["WorkItem"])
    assert stale >= 1
    node = neo4j_repo.get_node("ado:itest:proj1:wi-stale", ORG)
    assert node.props["isDeleted"] is True  # kept, not destroyed
    hits = neo4j_repo.search("stale", ["WorkItem"], ORG, 10)
    assert hits == []
    removed = neo4j_repo.delete_demo(ORG)
    assert removed >= 1
    assert neo4j_repo.get_node("enggraph:itest:svc-demo", ORG) is None
    # real records untouched by demo reset
    assert neo4j_repo.get_node("enggraph:itest:svc1", ORG) is not None
