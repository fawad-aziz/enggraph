"""MCP tool, authorization, and boundary tests (milestone 7, FR-10).

Every test runs the real tool code path against the graph the real sync worker
produced from the recorded Azure DevOps fixtures, with the real session
resolution for authorization. The graph reads go through the domain services the
REST API uses, so what these tests assert is what the API already guarantees.

What is pinned here:
- the tool surface is exactly the documented seven, all annotated read-only,
  none carrying a Cypher, organization, credential, or write parameter;
- arguments are validated, depth/limit clamped to the configured ceilings, and
  the bounds actually used are reported;
- results are structured JSON with provenance, timestamps, source URLs,
  truncation flags, and caveats;
- authorization is enforced: no token, an invalid/expired token, and
  cross-organization access are all refused;
- a store the server is not connected to is named in the refusal, not a crash
  inside a query on a missing store;
- no tool writes graph data, and no result contains a credential.
"""

from __future__ import annotations

import json
import logging

import pytest
from ado_fixtures import ORG, PAT
from api_support import (
    ADMIN_EMAIL,
    OTHER_ADMIN_EMAIL,
    OTHER_ORG,
    PROJECT_ID,
    VIEWER_EMAIL,
    build_app,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp_support import (
    AUTHOR_DANA,
    AUTHOR_OMAR,
    COMMIT_ABC,
    MCP_TOOL_NAMES,
    PR_ID,
    REPO_ID,
    TEAM_ID,
    WI_42,
    build_server,
    call,
    configure_catalog,
    context_for,
    user_token,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enggraph.auth.service import resolve_principal, revoke_session
from enggraph.graph.model import NODE_LABELS, RELATION_SEMANTICS, NodeUpsert
from enggraph.mcp.server import create_mcp_server
from enggraph.persistence.models import ImpactAnalysis
from enggraph.services.impact import INFERRED_CONFIDENCE

MCP_TOKEN_ENV = "ENGGRAPH_MCP_TOKEN"


@pytest.fixture()
def server(settings, engine, fake_driver, monkeypatch):
    """Server over the seeded fixture graph; the stdio env token is unset so the
    header-based path is the only credential in play."""
    monkeypatch.delenv(MCP_TOKEN_ENV, raising=False)
    app, repo = build_app(settings, engine, fake_driver, seed=True)
    return build_server(settings, engine, fake_driver, repo), repo


@pytest.fixture()
def tokens(settings, engine, server):
    """Users created after build_app has created the schema."""
    return {
        "admin": user_token(settings, engine, ADMIN_EMAIL, "admin", ORG),
        "viewer": user_token(settings, engine, VIEWER_EMAIL, "viewer", ORG),
        "other": user_token(settings, engine, OTHER_ADMIN_EMAIL, "admin", OTHER_ORG),
    }


@pytest.fixture()
def catalog(settings, engine, server, tokens):
    """Catalog data written through the real catalog service."""
    return configure_catalog(settings, engine, server[1])


# ------------------------------------------------------------------ surface


async def test_surface_is_the_documented_read_only_set(server):
    srv, _repo = server
    tools = await srv.list_tools()
    assert sorted(t.name for t in tools) == sorted(MCP_TOOL_NAMES)
    for tool in tools:
        # Annotated read-only, non-destructive, idempotent.
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        # Structured results (outputSchema) so clients get typed JSON.
        assert tool.output_schema is not None
        props = tool.input_schema["properties"]
        # No parameter can smuggle in Cypher, a target organization, or a credential.
        for name in props:
            assert name not in {"cypher", "query_cypher", "statement", "dsl", "sql"}
            assert "organization" not in name.lower()
            assert name.lower() not in {"pat", "token", "password", "secret", "session_token"}
    # No write surface at all.
    for name in [t.name.lower() for t in tools]:
        assert not any(
            word in name for word in ("write", "delete", "create", "upsert", "mutate", "cypher")
        )


async def test_no_generic_cypher_tool_exists(server):
    srv, _repo = server
    names = [t.name for t in await srv.list_tools()]
    assert "run_cypher" not in names
    # An unknown tool is refused, not silently accepted.
    with pytest.raises(ToolError) as exc:
        await srv.call_tool("run_cypher", {"cypher": "MATCH (n) RETURN n"})
    assert "Unknown tool" in str(exc.value)


# ------------------------------------------------------------------ search


async def test_search_returns_ingested_provider_data(server, tokens):
    srv, _repo = server
    body = await call(
        srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["viewer"]
    )
    assert body["organizationId"] == ORG
    assert body["requestedBy"] == VIEWER_EMAIL
    assert {item["entityId"] for item in body["items"]} == {PROJECT_ID, REPO_ID}
    assert body["total"] == 2
    assert body["truncated"] is False
    assert body["generatedAt"].endswith("Z")
    # Every item reports its own provenance-bearing fields.
    for item in body["items"]:
        assert item["entityType"] in NODE_LABELS
        assert item["lastSeenAt"]
    assert any("bounded" in caveat for caveat in body["caveats"])


async def test_search_type_filter_narrows(server, tokens):
    srv, _repo = server
    body = await call(
        srv,
        "search_engineering_entities",
        {"query": "commerce", "types": ["Repository"]},
        token=tokens["viewer"],
    )
    assert body["total"] == 1
    assert body["items"][0]["entityId"] == REPO_ID


async def test_search_limit_reports_truncation(server, tokens):
    srv, _repo = server
    body = await call(
        srv,
        "search_engineering_entities",
        {"query": "commerce", "limit": 1},
        token=tokens["viewer"],
    )
    assert len(body["items"]) == 1
    assert body["total"] == 2
    assert body["truncated"] is True


async def test_search_rejects_unknown_type(server, tokens):
    srv, _repo = server
    with pytest.raises(ToolError) as exc:
        await call(
            srv,
            "search_engineering_entities",
            {"query": "commerce", "types": ["Widget"]},
            token=tokens["viewer"],
        )
    assert "invalid_entity_type" in str(exc.value)


# ------------------------------------------------------------------ entity context


async def test_entity_context_reports_bounds_semantics_and_provenance(server, tokens):
    srv, repo = server
    body = await call(
        srv,
        "get_entity_context",
        {"entity_id": REPO_ID, "depth": 1, "limit": 10},
        token=tokens["viewer"],
    )
    assert body["subject"]["entityId"] == REPO_ID
    assert body["depth"] == 1 and body["limit"] == 10
    rel_types = {edge["relType"] for edge in body["relationships"]}
    assert rel_types <= set(RELATION_SEMANTICS)
    semantics = {entry["relType"]: entry for entry in body["relationshipSemantics"]}
    assert set(semantics) == rel_types
    for rel_type, entry in semantics.items():
        src, dst = RELATION_SEMANTICS[rel_type]
        assert entry["sourceLabel"] == src and entry["targetLabel"] == dst
        assert entry["meaning"]
    # Provider edges carry their provenance type.
    assert "PROVIDER_EXPLICIT" in body["provenanceTypes"]
    # Nodes keep their provider source links.
    assert any(url.startswith("https://dev.azure.com/") for url in body["sourceUrls"])
    assert body["truncated"] in (True, False)
    for node in body["entities"]:
        assert node["properties"].get("organizationId") == ORG


async def test_entity_context_is_organization_scoped(server, tokens):
    srv, repo = server
    # A node that exists only in the other organization.
    other_id = "enggraph:other:service:isolated"
    repo.upsert_nodes(
        [
            NodeUpsert(
                label="Service",
                id=other_id,
                props={"name": "Isolated", "organizationId": OTHER_ORG, "sourceSystem": "enggraph"},
                organization_id=OTHER_ORG,
            )
        ]
    )
    body = await call(srv, "get_entity_context", {"entity_id": other_id}, token=tokens["other"])
    assert body["organizationId"] == OTHER_ORG
    assert body["subject"]["entityId"] == other_id
    # The same id read from the first organization is a 404, not a leak.
    with pytest.raises(ToolError) as exc:
        await call(srv, "get_entity_context", {"entity_id": other_id}, token=tokens["viewer"])
    assert "entity_not_found" in str(exc.value)


async def test_depth_and_limit_are_clamped_to_the_configured_ceiling(
    server, tokens, settings, engine, fake_driver
):
    """A deployment ceiling below the requested bound is applied and reported."""
    _srv, repo = server
    tight = settings.model_copy(update={"max_depth": 2, "max_results": 20})
    tight_server = build_server(tight, engine, fake_driver, repo)
    body = await call(
        tight_server,
        "get_entity_context",
        {"entity_id": REPO_ID, "depth": 6, "limit": 50},
        token=tokens["viewer"],
    )
    assert body["depth"] == tight.max_depth
    assert body["limit"] == tight.max_results
    assert any("clamped" in caveat for caveat in body["caveats"])
    assert any(f"depth {tight.max_depth}" in caveat for caveat in body["caveats"])
    # The same request under the default ceilings uses the requested values.
    body = await call(
        server[0],
        "get_entity_context",
        {"entity_id": REPO_ID, "depth": 3, "limit": 10},
        token=tokens["viewer"],
    )
    assert body["depth"] == 3 and body["limit"] == 10
    assert not any("clamped" in caveat for caveat in body["caveats"])


async def test_out_of_range_arguments_are_refused(server, tokens):
    srv, _repo = server
    cases = [
        ({"entity_id": REPO_ID, "depth": 0}, "depth must be 1..10"),
        ({"entity_id": REPO_ID, "depth": 11}, "depth must be 1..10"),
        ({"entity_id": REPO_ID, "limit": 0}, "limit must be 1..200"),
        ({"entity_id": REPO_ID, "limit": 201}, "limit must be 1..200"),
        ({"entity_id": "   "}, "must not be empty"),
        ({"entity_id": "a b"}, "must not contain whitespace"),
        ({"entity_id": "x" * 300}, "longer than 200"),
    ]
    for args, expected in cases:
        with pytest.raises(ToolError) as exc:
            await call(srv, "get_entity_context", args, token=tokens["viewer"])
        assert expected in str(exc.value), args


# ------------------------------------------------------------------ repository context


async def test_repository_context_groups_provenance_and_labels(server, tokens, catalog):
    srv, _repo = server
    body = await call(
        srv,
        "get_repository_context",
        {"repository_id": REPO_ID, "limit": 10},
        token=tokens["viewer"],
    )
    assert body["repository"]["entityId"] == REPO_ID
    assert body["commits"], "the seeded graph contains commits"
    assert body["pullRequests"], "the seeded graph contains pull requests"
    assert {c["commit"]["entityId"] for c in body["commits"]} >= {COMMIT_ABC}
    assert {p["pullRequest"]["entityId"] for p in body["pullRequests"]} >= {PR_ID}
    # Work items reached through the repository's pull requests.
    assert WI_42 in {w["workItem"]["entityId"] for w in body["workItems"]}
    entry = next(w for w in body["workItems"] if w["workItem"]["entityId"] == WI_42)
    assert entry["viaPullRequest"]["entityId"] == PR_ID
    assert [e["relType"] for e in entry["path"]["relationships"]] == [
        "CONTAINS_PULL_REQUEST",
        "LINKED_TO",
    ]
    # Services implemented by the repository, explicit owners through OWNS.
    assert {s["service"]["entityId"] for s in body["services"]} == {catalog["checkout"]}
    owners = {o["team"]["entityId"] for o in body["explicitOwners"]}
    assert owners == {TEAM_ID}
    assert all(o["label"].startswith("explicit") for o in body["explicitOwners"])
    assert all(o["provenanceType"] == "MANUAL" for o in body["explicitOwners"])
    # Contributors are labelled as inferred, never as owners.
    contributors = {c["developer"]["entityId"] for c in body["inferredContributors"]}
    assert {AUTHOR_DANA, AUTHOR_OMAR} <= contributors
    assert all(c["label"].startswith("inferred") for c in body["inferredContributors"])
    assert all(c["confidence"] == INFERRED_CONFIDENCE for c in body["inferredContributors"])
    assert body["limit"] == 10
    assert any("limit` is applied to each group" in caveat for caveat in body["caveats"])


async def test_repository_context_refuses_the_wrong_label(server, tokens):
    srv, _repo = server
    with pytest.raises(ToolError) as exc:
        await call(
            srv, "get_repository_context", {"repository_id": PROJECT_ID}, token=tokens["viewer"]
        )
    assert "invalid_entity_type" in str(exc.value)


async def test_repository_context_reports_truncation(server, tokens):
    srv, _repo = server
    body = await call(
        srv,
        "get_repository_context",
        {"repository_id": REPO_ID, "limit": 1},
        token=tokens["viewer"],
    )
    assert len(body["commits"]) == 1
    assert body["truncated"] is True


# ------------------------------------------------------------------ dependencies


async def test_service_dependencies_are_directional(server, tokens, catalog):
    srv, _repo = server
    out = await call(
        srv,
        "get_service_dependencies",
        {"service_id": catalog["checkout"], "direction": "out", "depth": 2, "limit": 20},
        token=tokens["viewer"],
    )
    targets = {path["target"]["entityId"] for path in out["paths"]}
    # Direct dependency and the exposed API.
    assert {catalog["billing"], catalog["api"]} <= targets
    assert all(
        set(path["relTypes"]) <= {"DEPENDS_ON", "CONSUMES", "EXPOSES", "IMPLEMENTED_BY"}
        for path in out["paths"]
    )
    # billing consumes the API checkout exposes; that path runs the other way on the
    # API hop, so it is not reported as an outgoing dependency path of checkout.
    from_billing = await call(
        srv,
        "get_service_dependencies",
        {"service_id": catalog["billing"], "direction": "out", "depth": 2, "limit": 20},
        token=tokens["viewer"],
    )
    billing_targets = {path["target"]["entityId"] for path in from_billing["paths"]}
    assert catalog["api"] in billing_targets
    assert catalog["checkout"] not in billing_targets
    # `in` reports who depends on this service.
    into = await call(
        srv,
        "get_service_dependencies",
        {"service_id": catalog["billing"], "direction": "in", "depth": 2, "limit": 20},
        token=tokens["viewer"],
    )
    assert catalog["checkout"] in {path["target"]["entityId"] for path in into["paths"]}


async def test_dependencies_reject_an_invalid_direction(server, tokens, catalog):
    srv, _repo = server
    with pytest.raises(ToolError):
        await call(
            srv,
            "get_service_dependencies",
            {"service_id": catalog["checkout"], "direction": "sideways"},
            token=tokens["viewer"],
        )


# ------------------------------------------------------------------ change impact


async def test_change_impact_groups_findings_and_persists_the_report(
    server, tokens, catalog, engine
):
    srv, _repo = server
    body = await call(
        srv,
        "get_change_impact",
        {"entity_id": catalog["checkout"], "categories": ["INFERRED"], "depth": 4, "limit": 20},
        token=tokens["viewer"],
    )
    assert body["channel"] == "mcp"
    assert body["subjectId"] == catalog["checkout"]
    inferred = [f for f in body["findings"] if f["category"] == "INFERRED"]
    # The documented rule: services sharing an API.
    assert [f["entityId"] for f in inferred] == [catalog["billing"]]
    assert inferred[0]["confidence"] == INFERRED_CONFIDENCE
    assert [e["relType"] for e in inferred[0]["path"]["relationships"]] == ["EXPOSES", "CONSUMES"]
    assert any("limitations" in caveat.lower() or "bounded" in caveat for caveat in body["caveats"])
    assert any("persisted" in caveat for caveat in body["caveats"])
    # The audit record is organization-scoped and readable back.
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    row = session.execute(
        select(ImpactAnalysis).where(ImpactAnalysis.id == body["analysisId"])
    ).scalar_one()
    session.close()
    assert row.organization_id == ORG
    assert row.report["subjectId"] == catalog["checkout"]


async def test_change_impact_rejects_an_unknown_category(server, tokens):
    srv, _repo = server
    with pytest.raises(ToolError) as exc:
        await call(
            srv,
            "get_change_impact",
            {"entity_id": REPO_ID, "categories": ["SPECULATIVE"]},
            token=tokens["viewer"],
        )
    assert "invalid_category" in str(exc.value)


# ------------------------------------------------------------------ work items / owners


async def test_related_work_items_follow_relationship_paths(server, tokens):
    srv, _repo = server
    body = await call(
        srv,
        "get_related_work_items",
        {"entity_id": REPO_ID, "depth": 3, "limit": 20},
        token=tokens["viewer"],
    )
    ids = {item["workItem"]["entityId"] for item in body["workItems"]}
    assert WI_42 in ids
    entry = next(item for item in body["workItems"] if item["workItem"]["entityId"] == WI_42)
    assert entry["path"]["entities"][0]["entityId"] == REPO_ID
    assert "LINKED_TO" in entry["relTypes"]
    assert entry["provenanceTypes"]
    assert entry["workItem"]["sourceUrl"], "work items keep their provider link"
    assert set(body["relationshipAllowlist"]) == {
        "CONTAINS_PULL_REQUEST",
        "CONTAINS_COMMIT",
        "CHANGES",
        "LINKED_TO",
        "PARENT_OF",
        "RELATED_TO",
    }


async def test_component_owners_separates_configured_from_inferred(server, tokens, catalog):
    srv, _repo = server
    body = await call(
        srv,
        "get_component_owners",
        {"entity_id": REPO_ID, "depth": 2, "limit": 20},
        token=tokens["viewer"],
    )
    assert body["component"]["entityId"] == REPO_ID
    explicit = {entry["team"]["entityId"] for entry in body["explicitOwners"]}
    assert explicit == {TEAM_ID}
    assert all(entry["label"].startswith("explicit") for entry in body["explicitOwners"])
    inferred = {entry["developer"]["entityId"] for entry in body["inferredContributors"]}
    assert {AUTHOR_DANA, AUTHOR_OMAR} <= inferred
    assert all(entry["label"].startswith("inferred") for entry in body["inferredContributors"])
    assert all(entry["confidence"] == INFERRED_CONFIDENCE for entry in body["inferredContributors"])


async def test_unconfigured_component_reports_no_explicit_owners(server, tokens):
    srv, _repo = server
    body = await call(
        srv, "get_component_owners", {"entity_id": PROJECT_ID}, token=tokens["viewer"]
    )
    assert body["explicitOwners"] == []
    assert any("unconfigured" in caveat for caveat in body["caveats"])


# ------------------------------------------------------------------ authorization


async def test_missing_token_is_refused(server):
    srv, _repo = server
    with pytest.raises(ToolError) as exc:
        await call(srv, "search_engineering_entities", {"query": "commerce"})
    assert "missing_token" in str(exc.value)


async def test_invalid_token_is_refused(server, tokens):
    srv, _repo = server
    with pytest.raises(ToolError) as exc:
        await call(
            srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["viewer"] + "x"
        )
    assert "invalid_token" in str(exc.value)


async def test_revoked_session_is_refused(server, tokens, settings, engine):
    srv, _repo = server
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    principal = resolve_principal(session, settings, tokens["viewer"])
    assert principal is not None
    revoke_session(session, principal)
    session.close()
    with pytest.raises(ToolError) as exc:
        await call(
            srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["viewer"]
        )
    assert "invalid_token" in str(exc.value)


async def test_organization_is_taken_from_the_token_not_the_arguments(server, tokens):
    srv, _repo = server
    body = await call(
        srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["admin"]
    )
    assert body["organizationId"] == ORG
    other = await call(
        srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["other"]
    )
    # The other organization has no seeded data: no cross-tenant enumeration.
    assert other["organizationId"] == OTHER_ORG
    assert other["total"] == 0
    assert other["items"] == []


async def test_stdio_uses_the_operator_token_from_the_environment(server, tokens, monkeypatch):
    srv, _repo = server
    monkeypatch.setenv("ENGGRAPH_MCP_TOKEN", tokens["viewer"])
    body = await call(srv, "search_engineering_entities", {"query": "commerce"})
    assert body["organizationId"] == ORG
    assert body["requestedBy"] == VIEWER_EMAIL


async def test_stdio_env_token_is_resolved_by_the_same_session_code(server, monkeypatch):
    srv, _repo = server
    monkeypatch.setenv("ENGGRAPH_MCP_TOKEN", "not-a-minted-token")
    with pytest.raises(ToolError) as exc:
        await call(srv, "search_engineering_entities", {"query": "commerce"})
    assert "invalid_token" in str(exc.value)


async def test_a_request_without_a_header_never_inherits_the_process_token(
    server, tokens, monkeypatch
):
    """The operator env token is the stdio fallback only. A shared HTTP server with
    `ENGGRAPH_MCP_TOKEN` set must refuse an unauthenticated request, not serve it as
    the identity the process holds."""
    srv, _repo = server
    monkeypatch.setenv(MCP_TOKEN_ENV, tokens["admin"])
    ctx = context_for(srv, {})  # an HTTP request present, no Authorization header
    with pytest.raises(ToolError) as exc:
        await srv.call_tool("search_engineering_entities", {"query": "commerce"}, context=ctx)
    assert "missing_token" in str(exc.value)
    # The same server serves the header-borne token.
    body = await call(
        srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["viewer"]
    )
    assert body["requestedBy"] == VIEWER_EMAIL


async def test_authorization_is_checked_before_any_graph_read(server, tokens, monkeypatch):
    """A refused call never touches the store: the spy below stays at zero."""
    srv, repo = server
    calls = []
    original = repo.search

    def spy(q, labels, org_id, limit, offset=0):
        calls.append(org_id)
        return original(q, labels, org_id, limit, offset)

    monkeypatch.setattr(repo, "search", spy)
    with pytest.raises(ToolError):
        await call(srv, "search_engineering_entities", {"query": "commerce"})
    assert calls == []
    await call(srv, "search_engineering_entities", {"query": "commerce"}, token=tokens["viewer"])
    assert calls == [ORG]


# ------------------------------------------------------------------ read-only guarantees


async def test_no_tool_writes_graph_data(server, tokens, catalog):
    srv, repo = server
    before = repo.counts(ORG)
    args = [
        ("search_engineering_entities", {"query": "commerce"}),
        ("get_entity_context", {"entity_id": REPO_ID}),
        ("get_repository_context", {"repository_id": REPO_ID}),
        ("get_service_dependencies", {"service_id": catalog["checkout"]}),
        ("get_change_impact", {"entity_id": catalog["checkout"]}),
        ("get_related_work_items", {"entity_id": REPO_ID}),
        ("get_component_owners", {"entity_id": REPO_ID}),
    ]
    for name, tool_args in args:
        await call(srv, name, tool_args, token=tokens["admin"])
        assert repo.counts(ORG) == before, name
    # Edge counts too: node counts alone would miss an added relationship.
    edges = sum(
        len(repo.outgoing(node_id, ORG, [], 100))
        for node_id in [REPO_ID, TEAM_ID, catalog["checkout"]]
    )
    for name, tool_args in args:
        await call(srv, name, tool_args, token=tokens["admin"])
    assert (
        sum(
            len(repo.outgoing(node_id, ORG, [], 100))
            for node_id in [REPO_ID, TEAM_ID, catalog["checkout"]]
        )
        == edges
    )


async def test_results_never_contain_secrets(server, tokens, catalog, settings, engine):
    """Nothing credential-shaped reaches a tool result: the PAT that seeded the
    graph, the session secret that minted the caller's token, and the Neo4j
    password are all in play in the fixture settings, and none may appear."""
    srv, _repo = server
    payloads = []
    for name, tool_args in [
        ("search_engineering_entities", {"query": "commerce"}),
        ("get_entity_context", {"entity_id": REPO_ID}),
        ("get_repository_context", {"repository_id": REPO_ID}),
        ("get_service_dependencies", {"service_id": catalog["checkout"]}),
        ("get_change_impact", {"entity_id": catalog["checkout"]}),
        ("get_related_work_items", {"entity_id": REPO_ID}),
        ("get_component_owners", {"entity_id": REPO_ID}),
    ]:
        payloads.append(await call(srv, name, tool_args, token=tokens["admin"]))
    text = json.dumps(payloads)
    # The PAT is the credential the sync run used; the session secret minted the
    # caller's token; the Neo4j password is the store credential. None may appear.
    assert PAT not in text
    assert settings.session_secret not in text
    assert settings.neo4j_password not in text
    # No credential-shaped key is emitted anywhere in a result.
    keys = {k.lower() for payload in payloads for k in payload}
    assert not (keys & {"pat", "token", "password", "secret", "authorization", "session_token"})


async def test_access_logs_name_the_caller_and_carry_no_token(server, tokens, caplog):
    srv, _repo = server
    with caplog.at_level(logging.INFO, logger="enggraph.mcp"):
        await call(
            srv,
            "search_engineering_entities",
            {"query": "commerce", "limit": 5},
            token=tokens["viewer"],
        )
    records = [r for r in caplog.records if getattr(r, "tool", None)]
    assert records, "every tool call is logged"
    record = records[0]
    assert record.tool == "search_engineering_entities"
    assert record.organization_id == ORG
    assert record.limit == 5
    assert record.results <= 5
    for record in records:
        assert tokens["viewer"] not in str(record.__dict__)
        assert tokens["admin"] not in str(record.__dict__)


async def test_a_refused_call_is_an_error_the_client_can_read(server, tokens):
    """The tool raises `ToolError` with a `code: message` string; the SDK turns
    that into `isError: true` on the wire (asserted in test_mcp_http.py). In
    process the same failure surfaces as a raised ToolError, not a crash."""
    srv, _repo = server
    with pytest.raises(ToolError) as exc:
        await call(
            srv, "get_entity_context", {"entity_id": "does-not-exist"}, token=tokens["viewer"]
        )
    assert "entity_not_found" in str(exc.value)
    # The code is part of the message a client reads, not buried in a stack trace.
    assert str(exc.value).startswith("Error executing tool get_entity_context: entity_not_found:")


async def test_a_server_that_never_started_names_the_missing_stores(settings, monkeypatch):
    """A server built without Postgres/Neo4j is refused with the reason, so a client
    hears 'not connected' rather than a failure inside a query on a missing store."""
    monkeypatch.delenv(MCP_TOKEN_ENV, raising=False)
    srv = create_mcp_server(settings)
    with pytest.raises(ToolError) as exc:
        await srv.call_tool("search_engineering_entities", {"query": "commerce"})
    assert "server_not_started" in str(exc.value)
