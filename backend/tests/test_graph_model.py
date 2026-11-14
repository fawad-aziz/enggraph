from __future__ import annotations

import pytest

from enggraph.graph.ids import commit_id, pullrequest_id, repository_id, service_id, workitem_id
from enggraph.graph.in_memory import InMemoryGraphRepository
from enggraph.graph.model import EdgeUpsert, NodeUpsert

ORG = "acme"


def node(label: str, id: str, **props) -> NodeUpsert:
    props.setdefault("organizationId", ORG)
    return NodeUpsert(label=label, id=id, props=props, organization_id=ORG)


def edge(rel: str, src_label: str, src: str, dst_label: str, dst: str, **props) -> EdgeUpsert:
    props.setdefault("provenanceType", "PROVIDER_EXPLICIT")
    props.setdefault("observedAt", "2026-01-01T00:00:00Z")
    return EdgeUpsert(
        rel_type=rel,
        src_label=src_label,
        src_id=src,
        dst_label=dst_label,
        dst_id=dst,
        props=props,
        organization_id=ORG,
    )


P1 = "ado:acme:proj1"
R1 = "ado:acme:proj1:repo1"
PR1 = "ado:acme:proj1:repo1:pr1"
C1 = "ado:acme:proj1:repo1:c1"
WI1 = "ado:acme:proj1:wi1"
SVC1 = "enggraph:acme:svc1"
SVC2 = "enggraph:acme:svc2"
API1 = "enggraph:acme:api1"


def fixture_nodes() -> list:
    return [
        node("Organization", "ado:azure:acme", name="Acme", provider="azure_devops"),
        node("Project", P1, name="Commerce", sourceUrl="https://dev.azure.com/acme/Commerce"),
        node("Team", "ado:acme:team1", name="Platform"),
        node("Developer", "ado:acme:dev1", displayName="Dana Diaz"),
        node("Repository", R1, name="commerce-api", defaultBranch="main"),
        node("WorkItem", WI1, title="Support identity providers", state="New"),
        node("PullRequest", PR1, title="Add identity provider support", status="completed"),
        node("Commit", C1, hash="abc123", message="identity provider abstraction"),
        node("Service", SVC1, name="Identity Service", criticality="high"),
        node("Service", SVC2, name="Commerce Service", criticality="high"),
        node("API", API1, name="Identity API v2", version="2.0"),
        # second organization — must never leak into acme queries
        node("Project", "ado:other:proj9", name="OtherCommerce", organizationId="other"),
        node(
            "Repository", "ado:other:proj9:repo9", name="commerce-api-clone", organizationId="other"
        ),
    ]


def fixture_edges() -> list:
    manual = {"provenanceType": "MANUAL", "createdBy": "admin"}
    return [
        edge("HAS_PROJECT", "Organization", "ado:azure:acme", "Project", P1),
        edge("HAS_TEAM", "Organization", "ado:azure:acme", "Team", "ado:acme:team1"),
        edge("HAS_MEMBER", "Team", "ado:acme:team1", "Developer", "ado:acme:dev1"),
        edge("CONTAINS_REPOSITORY", "Project", P1, "Repository", R1),
        edge("CONTAINS_PULL_REQUEST", "Repository", R1, "PullRequest", PR1),
        edge("CONTAINS_COMMIT", "Repository", R1, "Commit", C1),
        edge("AUTHORED", "Developer", "ado:acme:dev1", "Commit", C1),
        edge("CREATED_BY", "PullRequest", PR1, "Developer", "ado:acme:dev1"),
        edge("REVIEWED_BY", "PullRequest", PR1, "Developer", "ado:acme:dev1"),
        edge("LINKED_TO", "PullRequest", PR1, "WorkItem", WI1),
        edge("CHANGES", "PullRequest", PR1, "Commit", C1),
        edge("OWNS", "Team", "ado:acme:team1", "Service", SVC1, **manual),
        edge("IMPLEMENTED_BY", "Service", SVC1, "Repository", R1, **manual),
        edge("EXPOSES", "Service", SVC1, "API", API1, **manual),
        edge("CONSUMES", "Service", SVC2, "API", API1, **manual),
        edge("DEPENDS_ON", "Service", SVC2, "Service", SVC1, **manual),
    ]


def build_fixture_repo() -> InMemoryGraphRepository:
    repo = InMemoryGraphRepository()
    repo.upsert_nodes(fixture_nodes())
    repo.upsert_edges(fixture_edges())
    return repo


def test_id_construction_formats():
    assert repository_id("acme", "proj1", "r-uuid") == "ado:acme:proj1:repository:r-uuid"
    assert workitem_id("acme", "proj1", "42") == "ado:acme:proj1:workitem:42"
    assert pullrequest_id("acme", "proj1", "r-uuid", "7") == "ado:acme:proj1:pullrequest:r-uuid:7"
    assert (
        commit_id("acme", "proj1", "r-uuid", "deadbeef") == "ado:acme:proj1:commit:r-uuid:deadbeef"
    )
    assert service_id("acme", "uuid1") == "enggraph:acme:service:uuid1"


def test_id_rejects_colons():
    with pytest.raises(ValueError):
        repository_id("acme:evil", "proj1", "r1")


def test_edge_direction_validation():
    bad = EdgeUpsert(
        rel_type="LINKED_TO",
        src_label="WorkItem",
        src_id="x",
        dst_label="PullRequest",
        dst_id="y",
        props={"provenanceType": "PROVIDER_EXPLICIT"},
        organization_id=ORG,
    )
    with pytest.raises(ValueError, match="LINKED_TO must go PullRequest -> WorkItem"):
        bad.validate_endpoints()


def test_edge_requires_provenance():
    with pytest.raises(ValueError, match="provenanceType"):
        EdgeUpsert(
            rel_type="DEPENDS_ON",
            src_label="Service",
            src_id="s1",
            dst_label="Service",
            dst_id="s2",
            props={},
            organization_id=ORG,
        ).validate_endpoints()


def test_unknown_label_and_rel_rejected():
    with pytest.raises(ValueError):
        NodeUpsert(label="Incident", id="x", props={}, organization_id=ORG)
    with pytest.raises(ValueError):
        EdgeUpsert(
            rel_type="RELATED_GENERIC",
            src_label="Service",
            src_id="a",
            dst_label="Service",
            dst_id="b",
            props={"provenanceType": "MANUAL"},
            organization_id=ORG,
        )


def test_repeated_upserts_create_no_duplicates():
    repo = build_fixture_repo()
    before = repo.counts(ORG)
    # Full sync rerun with identical data must be idempotent.
    repo.upsert_nodes(fixture_nodes())
    repo.upsert_edges(fixture_edges())
    assert repo.counts(ORG) == before
    # No duplicate directed edges: (rel, src, dst) keys are unique.
    all_edges = repo.outgoing("ado:acme:proj1:repo1:pr1", ORG, [], 100) + repo.incoming(
        "ado:acme:proj1:repo1:pr1", ORG, [], 100
    )
    keys = [(e.rel_type, e.src, e.dst) for e in all_edges]
    assert len(keys) == len(set(keys))


def test_edge_merge_preserves_and_updates_props():
    repo = build_fixture_repo()
    # upsert the same edge again with extra evidence props
    repo.upsert_edges(
        [
            edge(
                "LINKED_TO",
                "PullRequest",
                "ado:acme:proj1:repo1:pr1",
                "WorkItem",
                "ado:acme:proj1:wi1",
                sourceRecordId="pr-1-workitem-9",
            )
        ]
    )
    outs = repo.outgoing("ado:acme:proj1:repo1:pr1", ORG, ["LINKED_TO"], 10)
    assert len(outs) == 1
    assert outs[0].props["provenanceType"] == "PROVIDER_EXPLICIT"
    assert outs[0].props["sourceRecordId"] == "pr-1-workitem-9"
    assert outs[0].props["observedAt"] == "2026-01-01T00:00:00Z"


def test_edge_to_missing_node_is_skipped():
    repo = InMemoryGraphRepository()
    created = repo.upsert_edges(
        [edge("LINKED_TO", "PullRequest", "missing-pr", "WorkItem", "missing-wi")]
    )
    assert created == 0
    assert repo.counts(ORG) == {}


def test_search_filters_by_label_org_and_text():
    repo = build_fixture_repo()
    hits = repo.search("identity", ["WorkItem", "PullRequest", "Service", "API"], ORG, 10)
    labels = {h.label for h in hits}
    assert "WorkItem" in labels and "PullRequest" in labels
    assert all(h.props["organizationId"] == ORG for h in hits)
    hits_all = repo.search("commerce", ["Project", "Repository"], ORG, 10)
    # 'commerce-api-clone' belongs to org 'other' → must not appear
    assert all(h.props["organizationId"] == ORG for h in hits_all)
    assert {h.id for h in hits_all} == {"ado:acme:proj1", "ado:acme:proj1:repo1"}


ALL_LABELS = [
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


def test_search_pagination():
    repo = build_fixture_repo()
    page1 = repo.search("", ALL_LABELS, ORG, 2, 0)
    page2 = repo.search("", ALL_LABELS, ORG, 2, 2)
    assert len(page1) == 2 and len(page2) == 2
    assert page1[0].id != page2[0].id
    total = repo.search_count("", ALL_LABELS, ORG)
    assert total == len(repo.search("", ALL_LABELS, ORG, 100, 0))
    assert repo.search_count("", ["Service"], ORG) == 2


def test_get_node_org_isolation():
    repo = build_fixture_repo()
    assert repo.get_node("ado:other:proj9", ORG) is None
    assert repo.get_node("ado:other:proj9", "other") is not None


def test_outgoing_direction_and_org_scope():
    repo = build_fixture_repo()
    outs = repo.outgoing("ado:acme:proj1:repo1:pr1", ORG, ["LINKED_TO", "CHANGES"], 10)
    assert {(e.rel_type, e.dst) for e in outs} == {
        ("LINKED_TO", "ado:acme:proj1:wi1"),
        ("CHANGES", "ado:acme:proj1:repo1:c1"),
    }
    # incoming to WorkItem is the PR (direction PR -> WorkItem preserved)
    ins = repo.incoming("ado:acme:proj1:wi1", ORG, ["LINKED_TO"], 10)
    assert [(e.rel_type, e.src) for e in ins] == [("LINKED_TO", "ado:acme:proj1:repo1:pr1")]


def test_neighborhood_is_bounded_and_filtered():
    repo = build_fixture_repo()
    depth1 = repo.neighborhood(R1, ORG, [], ["CONTAINS_PULL_REQUEST"], 1, 50)
    ids = {n.id for n in depth1.nodes}
    assert ids == {R1, PR1}
    depth2 = repo.neighborhood(R1, ORG, [], ["CONTAINS_PULL_REQUEST", "LINKED_TO"], 2, 50)
    assert WI1 in {n.id for n in depth2.nodes}
    depth1_no_pr = repo.neighborhood(R1, ORG, [], ["CONTAINS_PULL_REQUEST"], 1, 1)
    assert len(depth1_no_pr.nodes) <= 2  # start + limited expansion
    # node type filter
    only_services = repo.neighborhood(SVC2, ORG, ["Service"], ["DEPENDS_ON"], 2, 50)
    assert all(n.label == "Service" for n in only_services.nodes)
    assert SVC1 in {n.id for n in only_services.nodes}


def test_paths_explain_inclusion():
    repo = build_fixture_repo()
    # svc2 -CONSUMES-> api1 <-EXPOSES- svc1: 2-edge path, traversed undirected
    paths = repo.find_paths(
        "enggraph:acme:svc2", "enggraph:acme:svc1", ORG, ["CONSUMES", "EXPOSES"], 2, 10
    )
    assert paths
    p = paths[0]
    assert p.nodes[0].id == "enggraph:acme:svc2" and p.nodes[-1].id == "enggraph:acme:svc1"
    # every edge must connect consecutive path vertices (explainable inclusion)
    for i in range(len(p.edges)):
        assert p.edges[i].src in (p.nodes[i].id, p.nodes[i + 1].id)
        assert p.edges[i].dst in (p.nodes[i].id, p.nodes[i + 1].id)
    # bounded depth: depth 1 finds nothing for a 2-edge path
    assert (
        repo.find_paths(
            "enggraph:acme:svc2", "enggraph:acme:svc1", ORG, ["CONSUMES", "EXPOSES"], 1, 10
        )
        == []
    )


def test_long_bounded_path_across_ado_and_manual_edges():
    repo = build_fixture_repo()
    # wi1 <-LINKED_TO- pr1 -CHANGES-> c1 <-CONTAINS_COMMIT- repo1 <-IMPLEMENTED_BY- svc1
    paths = repo.find_paths(
        "ado:acme:proj1:wi1",
        "enggraph:acme:svc1",
        ORG,
        ["LINKED_TO", "CHANGES", "CONTAINS_COMMIT", "IMPLEMENTED_BY"],
        4,
        10,
    )
    assert paths
    p = paths[0]
    assert [n.id for n in p.nodes] == [
        "ado:acme:proj1:wi1",
        "ado:acme:proj1:repo1:pr1",
        "ado:acme:proj1:repo1:c1",
        "ado:acme:proj1:repo1",
        "enggraph:acme:svc1",
    ]
    # depth 3 cannot reach a 4-edge target
    chain = ["LINKED_TO", "CHANGES", "CONTAINS_COMMIT", "IMPLEMENTED_BY"]
    assert repo.find_paths(WI1, SVC1, ORG, chain, 3, 10) == []


def test_mark_not_seen_reconciliation():
    repo = build_fixture_repo()
    repo.upsert_nodes(
        [
            node(
                "WorkItem",
                "ado:acme:proj1:wi2",
                title="Old ticket",
                lastSeenAt="2026-01-01T00:00:00Z",
            ),
            node(
                "WorkItem",
                "ado:acme:proj1:wi3",
                title="Fresh ticket",
                lastSeenAt="2026-11-01T00:00:00Z",
            ),
        ]
    )
    stale = repo.mark_not_seen(ORG, "2026-06-01T00:00:00Z", ["WorkItem"])
    assert stale == 1
    assert repo.get_node("ado:acme:proj1:wi2", ORG).props["isDeleted"] is True
    # deactivation keeps the node (historical context) and hides from search
    hits = repo.search("ticket", ["WorkItem"], ORG, 10)
    assert {h.id for h in hits} == {"ado:acme:proj1:wi3"}


def test_demo_reset_removes_only_demo_records():
    repo = build_fixture_repo()
    repo.upsert_nodes(
        [
            node(
                "Service", "enggraph:acme:svc9", name="Demo svc", demoData=True, organizationId=ORG
            ),
        ]
    )
    repo.upsert_edges(
        [
            edge(
                "DEPENDS_ON",
                "Service",
                SVC2,
                "Service",
                "enggraph:acme:svc9",
                provenanceType="MANUAL",
            ),
        ]
    )
    removed = repo.delete_demo(ORG)
    assert removed == 1
    assert repo.get_node("enggraph:acme:svc9", ORG) is None
    # edge to removed node is gone (DETACH semantics)
    outs = repo.outgoing("enggraph:acme:svc2", ORG, ["DEPENDS_ON"], 10)
    assert "enggraph:acme:svc9" not in {e.dst for e in outs}


def test_counts_per_org():
    repo = build_fixture_repo()
    acme = repo.counts(ORG)
    assert acme["Project"] == 1 and acme["Service"] == 2
    other = repo.counts("other")
    assert other["Project"] == 1 and other.get("Service", 0) == 0
