"""Demo mode from a clean database (spec §8, milestone 8).

Demo fixtures are the only data this application writes that was not read from a
provider, so the promises spec §8 makes are pinned here:

- the dataset is a small synthetic organization: more than one team and
  repository, work items, pull requests, commits, services, APIs, cross-service
  relationships, and provenance on every relationship;
- every record is labelled as demo data, and the label is visible through the API
  and through MCP, not only in the source file;
- data is loaded through the production write path (`persist_rows` on the same
  graph repository the sync worker uses), not a special demo path;
- the loader refuses an organization that already holds provider data, so demo
  fixtures never mix into a live organization;
- reconciliation never touches demo nodes, and reset removes exactly the demo
  records of one organization;
- one impact-analysis scenario runs on demo data, through both surfaces;
- the load/reset/user scripts exist, run, and refuse loudly on a broken
  configuration.

Neo4j and Postgres are not reachable in this environment, so the stores are the
same two doubles the API tests use (`InMemoryGraphRepository` + SQLite). The
scripts' `run()` functions are exercised against them; the process-level behaviour
that needs no store (`--help`, a configuration refusal) is exercised by running each
script as a subprocess. The same `demo.py` code path against a real Neo4j is
covered by `test_neo4j_integration.py`'s gating: it runs when
`ENGGRAPH_NEO4J_TEST_URI` is available and reports why it did not when it is not.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import create_user as user_script
import demo_load
import demo_reset
import pytest
from ado_fixtures import ORG
from api_support import ADMIN_EMAIL, PASSWORD, bearer, build_app
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from mcp_support import build_server, call, context_for, user_token
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from enggraph.auth.service import login as session_login
from enggraph.demo import (
    DEMO_ACTOR,
    DEMO_NOTE,
    DEMO_ORG,
    DEMO_SOURCE_SYSTEM,
    DemoConflictError,
    demo_dataset,
    load_demo,
    reset_demo,
)
from enggraph.graph.ids import demo_id
from enggraph.graph.in_memory import InMemoryGraphRepository
from enggraph.graph.model import NODE_LABELS
from enggraph.persistence.engine import build_sessionmaker
from enggraph.persistence.models import AuditRecord, Base, OrganizationConfig, User
from enggraph.services.impact import INFERRED_CONFIDENCE

DEMO_ADMIN = "demo-admin@demo.test"
DEMO_STAMP = "2026-11-01T00:00:00Z"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def did(slug: str) -> str:
    """Canonical id of one demo record."""
    return demo_id(DEMO_ORG, slug)


@pytest.fixture()
def stores(settings, engine, fake_driver):
    """Provider graph in `acme` (real sync), demo graph in `demo`, app + MCP server."""
    app, repo = build_app(settings, engine, fake_driver)
    maker = build_sessionmaker(engine)
    session: Session = maker()
    result = demo_load.run(repo, session, DEMO_ORG, replace=False)
    session.close()
    demo_token = user_token(settings, engine, DEMO_ADMIN, "admin", DEMO_ORG)
    acme_token = user_token(settings, engine, ADMIN_EMAIL, "admin", ORG)
    server = build_server(settings, engine, fake_driver, repo)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client,
            repo=repo,
            settings=settings,
            engine=engine,
            maker=maker,
            server=server,
            load=result,
            demo_token=demo_token,
            acme_token=acme_token,
        )


def audit_actions(session: Session, action: str, org: str = DEMO_ORG) -> list[AuditRecord]:
    stmt = select(AuditRecord).where(
        AuditRecord.action == action, AuditRecord.organization_id == org
    )
    return list(session.execute(stmt).scalars().all())


def demo_nodes(repo: InMemoryGraphRepository, org: str = DEMO_ORG) -> list:
    """Every node in `org`, across all labels (bounded per label)."""
    return [node for label in NODE_LABELS for node in repo.list_by_label(label, org, 100)]


def test_the_dataset_is_a_small_synthetic_organization_and_every_record_is_labelled():
    nodes, edges = demo_dataset(DEMO_ORG, DEMO_STAMP)
    counts = Counter(node.label for node in nodes)
    assert counts["Organization"] == 1
    assert counts["Project"] == 2
    assert counts["Team"] == 3
    assert counts["Repository"] == 3
    assert counts["WorkItem"] == 5
    assert counts["PullRequest"] == 2
    assert counts["Commit"] == 4
    assert counts["Service"] == 4
    assert counts["API"] == 4
    for node in nodes:
        assert node.props["demoData"] is True
        assert node.props["sourceSystem"] == DEMO_SOURCE_SYSTEM
        assert node.props["demoNote"] == DEMO_NOTE
        assert node.props["lastSeenAt"] == DEMO_STAMP
        # A synthetic record must not point at a real provider page.
        assert "sourceUrl" not in node.props
        # Demo ids are namespaced apart from provider ids.
        assert node.id.startswith("demo:demo:")
    by_type = Counter(edge.rel_type for edge in edges)
    for rel in (
        "HAS_PROJECT",
        "HAS_TEAM",
        "HAS_MEMBER",
        "CONTAINS_REPOSITORY",
        "AUTHORED",
        "CONTAINS_COMMIT",
        "CONTAINS_PULL_REQUEST",
        "CREATED_BY",
        "REVIEWED_BY",
        "CHANGES",
        "LINKED_TO",
        "PARENT_OF",
        "RELATED_TO",
        "OWNS",
        "IMPLEMENTED_BY",
        "EXPOSES",
        "CONSUMES",
        "DEPENDS_ON",
    ):
        assert by_type[rel] >= 1, f"{rel} is part of the documented dataset"
    for edge in edges:
        edge.validate_endpoints()
        assert edge.props["sourceSystem"] == DEMO_SOURCE_SYSTEM
        assert edge.props["sourceRecordId"]
        assert edge.props["observedAt"] == DEMO_STAMP
        assert edge.props["createdBy"] == DEMO_ACTOR
        assert edge.props["demoNote"] == DEMO_NOTE
    # Provenance is recorded, not implied: provider-shaped edges, catalog edges, and
    # a runtime-observed dependency are all present and all distinguishable.
    assert {edge.props["provenanceType"] for edge in edges} == {
        "PROVIDER_EXPLICIT",
        "MANUAL",
        "RUNTIME_OBSERVED",
    }


def test_demo_data_loads_from_a_clean_database_through_the_production_write_path(engine):
    Base.metadata.create_all(engine)
    clean = InMemoryGraphRepository()
    session: Session = build_sessionmaker(engine)()
    nodes, edges = demo_dataset(DEMO_ORG, DEMO_STAMP)
    result = demo_load.run(clean, session, DEMO_ORG, replace=False)
    assert result["nodesWritten"] == len(nodes)
    assert result["edgesWritten"] == len(edges)
    assert result["demoData"] is True
    assert result["counts"]["Service"] == 4
    assert result["counts"]["Developer"] == 4
    for node in demo_nodes(clean):
        assert node.props["demoData"] is True
        assert node.props["sourceSystem"] == DEMO_SOURCE_SYSTEM
    config = session.execute(
        select(OrganizationConfig).where(OrganizationConfig.organization_id == DEMO_ORG)
    ).scalar_one()
    assert "demo" in config.display_name
    loaded = audit_actions(session, "demo.load")
    assert len(loaded) == 1
    assert loaded[0].actor == DEMO_ACTOR
    assert loaded[0].details["demo_data"] is True
    # The audit trail reports what was written, not a hand-typed number.
    assert loaded[0].details["nodes_written"] == len(nodes)
    session.close()


def test_the_api_labels_every_demo_record_it_returns(stores):
    headers = bearer(stores.demo_token)
    items = stores.client.get("/api/v1/organizations", headers=headers).json()["items"]
    assert len(items) == 1
    assert items[0]["organizationId"] == DEMO_ORG
    assert "demo" in items[0]["displayName"]
    assert items[0]["graphCounts"]["Service"] == 4
    subject = stores.client.get(f"/api/v1/entities/{did('service-orders')}", headers=headers)
    assert subject.status_code == 200
    body = subject.json()
    assert body["isDemoData"] is True
    assert body["sourceSystem"] == DEMO_SOURCE_SYSTEM
    assert body["sourceUrl"] is None
    hits = stores.client.get("/api/v1/search", params={"q": "demo"}, headers=headers).json()
    assert hits["total"] > 0
    assert all(item["isDemoData"] is True for item in hits["items"])
    neighborhood = stores.client.get(
        f"/api/v1/entities/{did('service-orders')}/neighborhood",
        headers=headers,
        params={"depth": 2, "limit": 50},
    ).json()
    assert neighborhood["nodes"]
    assert all(node["isDemoData"] is True for node in neighborhood["nodes"])
    assert all(edge["sourceSystem"] == DEMO_SOURCE_SYSTEM for edge in neighborhood["edges"])


def test_one_demo_service_explains_a_useful_impact_scenario(stores):
    """Orders exposes POST /orders (consumed by Checkout) and consumes POST /tokens
    (exposed by Identity): the two services that share an API with it."""
    headers = bearer(stores.demo_token)
    response = stores.client.post(
        "/api/v1/impact-analysis",
        headers=headers,
        json={"subjectId": did("service-orders"), "depth": 4, "limit": 50},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["organizationId"] == DEMO_ORG
    assert data["subjectName"] == "Orders Service"
    assert data["counts"]["INFERRED"] == 2
    expected = {
        did("service-checkout"): (did("api-create-order"), ["EXPOSES", "CONSUMES"]),
        did("service-identity"): (did("api-token"), ["CONSUMES", "EXPOSES"]),
    }
    inferred = [f for f in data["findings"] if f["category"] == "INFERRED"]
    assert {f["entityId"] for f in inferred} == set(expected)
    for finding in inferred:
        shared_api, rel_types = expected[finding["entityId"]]
        assert [e["entityId"] for e in finding["path"]["entities"]] == [
            did("service-orders"),
            shared_api,
            finding["entityId"],
        ]
        assert [e["relType"] for e in finding["path"]["relationships"]] == rel_types
        assert finding["depth"] == 2
        assert finding["confidence"] == INFERRED_CONFIDENCE
        # The catalog links are manual, so the report says its evidence is manual.
        assert finding["hasManualEvidence"] is True
        assert finding["provenanceTypes"] == ["MANUAL"]
        assert finding["sourceUrl"] is None
    dependency = {f["entityId"] for f in data["findings"] if f["category"] == "DEPENDENCY"}
    assert did("service-payments") in dependency
    assert any("depth 4" in line for line in data["limitations"])
    reopened = stores.client.get(f"/api/v1/impact-analysis/{data['analysisId']}", headers=headers)
    assert reopened.status_code == 200
    assert reopened.json()["subjectId"] == did("service-orders")


async def test_mcp_reads_the_demo_graph_and_labels_what_it_returns(stores):
    token = stores.demo_token
    hits = await call(
        stores.server,
        "search_engineering_entities",
        {"query": "Checkout", "types": ("Service",)},
        token=token,
    )
    assert hits["organizationId"] == DEMO_ORG
    assert hits["items"]
    assert all(item["isDemoData"] is True for item in hits["items"])
    assert any("bounded" in caveat for caveat in hits["caveats"])

    owners = await call(
        stores.server,
        "get_component_owners",
        {"entity_id": did("service-checkout"), "depth": 2},
        token=token,
    )
    explicit = owners["explicitOwners"]
    assert [entry["team"]["entityId"] for entry in explicit] == [did("team-checkout")]
    assert explicit[0]["provenanceTypes"] == ["MANUAL"]
    assert all(entry["label"].startswith("explicit") for entry in explicit)

    deps = await call(
        stores.server,
        "get_service_dependencies",
        {"service_id": did("service-orders"), "direction": "out", "depth": 3},
        token=token,
    )
    reached = {entry["target"]["entityId"] for entry in deps["paths"]}
    assert did("service-payments") in reached
    assert all(entry["target"]["isDemoData"] is True for entry in deps["paths"])

    repo_context = await call(
        stores.server,
        "get_repository_context",
        {"repository_id": did("repository-checkout")},
        token=token,
    )
    assert {entry["commit"]["entityId"] for entry in repo_context["commits"]} == {
        did("commit-aa11"),
        did("commit-bb22"),
    }

    work_items = await call(
        stores.server,
        "get_related_work_items",
        {"entity_id": did("repository-checkout"), "depth": 2},
        token=token,
    )
    assert {entry["workItem"]["entityId"] for entry in work_items["workItems"]} == {
        did("workitem-110"),
        did("workitem-111"),
    }

    report = await call(
        stores.server,
        "get_change_impact",
        {"entity_id": did("service-orders"), "depth": 4, "limit": 50},
        token=token,
    )
    assert report["channel"] == "mcp"
    assert report["organizationId"] == DEMO_ORG
    assert {f["entityId"] for f in report["findings"] if f["category"] == "INFERRED"} == {
        did("service-checkout"),
        did("service-identity"),
    }


async def test_an_acme_caller_never_sees_the_demo_graph(stores):
    response = stores.client.get(
        f"/api/v1/entities/{did('service-orders')}", headers=bearer(stores.acme_token)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "entity_not_found"
    ctx = context_for(stores.server, bearer(stores.acme_token))
    with pytest.raises(ToolError) as raised:
        # `MCPServer.call_tool` propagates ToolError; the HTTP transport is what
        # turns it into `isError: true`.
        await stores.server.call_tool(
            "get_entity_context", {"entity_id": did("service-orders")}, context=ctx
        )
    assert "entity_not_found" in str(raised.value)
    # The refusal names the id, never the session secret.
    assert stores.settings.session_secret not in str(raised.value)


def test_a_provider_pass_never_reconciles_demo_nodes(stores):
    cutoff = "2099-01-01T00:00:00Z"
    live = demo_nodes(stores.repo, ORG)
    # A provider pass is evidence about provider-observed nodes only, so the
    # observable count is provider nodes with an observation time — not every node.
    provider_seen = [
        n
        for n in live
        if n.props.get("sourceSystem") == "azure_devops" and n.props.get("lastSeenAt") is not None
    ]
    assert provider_seen
    assert len(provider_seen) < len(live)
    assert stores.repo.mark_not_seen(DEMO_ORG, cutoff, list(NODE_LABELS)) == 0
    assert stores.repo.mark_not_seen(ORG, cutoff, list(NODE_LABELS)) == len(provider_seen)
    for node in demo_nodes(stores.repo):
        assert node.props.get("isDeleted") is not True
        assert node.props["demoData"] is True


def test_reset_removes_exactly_the_demo_records(stores):
    session: Session = stores.maker()
    before = dict(stores.repo.counts(ORG))
    demo_total = sum(stores.repo.counts(DEMO_ORG).values())
    result = reset_demo(stores.repo, session, org=DEMO_ORG)
    assert result["nodesRemoved"] == demo_total
    assert stores.repo.counts(DEMO_ORG) == {}
    assert stores.repo.get_node(did("service-orders"), DEMO_ORG) is None
    # Edges that touched demo nodes are gone with them.
    assert stores.repo.outgoing(did("service-orders"), DEMO_ORG, ["EXPOSES"], 10) == []
    # The provider organization is untouched, counts and nodes alike.
    assert stores.repo.counts(ORG) == before
    reset = audit_actions(session, "demo.reset")
    assert len(reset) == 1
    assert reset[0].details["nodes_removed"] == demo_total
    # A reset can be aimed at a live organization: it removes demo records only.
    aimed = reset_demo(stores.repo, session, org=ORG)
    assert aimed["nodesRemoved"] == 0
    assert stores.repo.counts(ORG) == before
    session.close()


def test_the_loader_refuses_an_organization_that_already_holds_provider_data(stores):
    session: Session = stores.maker()
    provider_nodes = sum(stores.repo.counts(ORG).values())
    with pytest.raises(DemoConflictError) as raised:
        load_demo(stores.repo, session, org=ORG, actor=DEMO_ACTOR)
    assert "live organization" in str(raised.value)
    assert sum(stores.repo.counts(ORG).values()) == provider_nodes
    assert [n for n in demo_nodes(stores.repo, ORG) if n.props.get("demoData") is True] == []
    # Nothing was written, so nothing was audited.
    assert audit_actions(session, "demo.load", org=ORG) == []
    session.close()


def test_the_replace_flag_is_a_safe_reload(stores):
    session: Session = stores.maker()
    first = sum(stores.repo.counts(DEMO_ORG).values())
    result = demo_load.run(stores.repo, session, DEMO_ORG, replace=True)
    assert result["nodesWritten"] == len(demo_dataset(DEMO_ORG, DEMO_STAMP)[0])
    assert sum(stores.repo.counts(DEMO_ORG).values()) == first
    assert len(audit_actions(session, "demo.reset")) == 1
    assert len(audit_actions(session, "demo.load")) == 2
    session.close()


def test_the_scripts_run_and_refuse_loudly_on_a_broken_configuration():
    """Process-level contract: the commands the README lists are the real entry points.

    No store is reachable here, so what is pinned is the behaviour that does not need
    one: `--help`, and a configuration problem reported on stderr with a non-zero exit
    before any connection is opened.
    """
    env = {
        **os.environ,
        "ENGGRAPH_MODE": "demo",
        "POSTGRES_DSN": "sqlite://",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "",
        "ENGGRAPH_SESSION_SECRET": "test-secret-32-bytes-long-aaaaaaa",
    }
    for name, needle in (
        ("demo_load.py", "--replace"),
        ("demo_reset.py", "--org"),
        ("create_user.py", "--email"),
    ):
        help_run = subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--help"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert help_run.returncode == 0, help_run.stderr
        assert needle in help_run.stdout
    refused = subprocess.run(
        [sys.executable, str(SCRIPTS / "demo_load.py")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert refused.returncode == 2
    assert "NEO4J_PASSWORD is empty" in refused.stderr
    # The refusal happened before any store was contacted: the load never ran.
    assert "demo data loaded" not in refused.stdout


def test_the_user_script_stores_a_hash_and_never_echoes_the_password(engine, settings, capsys):
    Base.metadata.create_all(engine)
    maker = build_sessionmaker(engine)
    args = ["--email", "ops@demo.test", "--role", "admin", "--org", DEMO_ORG, "--min", "12"]
    short = user_script.main(args, engine=engine, read_password=lambda _p: "shorty")
    assert short == 2
    assert "at least 12" in capsys.readouterr().err
    code = user_script.main(args, engine=engine, read_password=lambda _p: PASSWORD)
    assert code == 0
    printed = capsys.readouterr().out
    assert PASSWORD not in printed
    assert "ops@demo.test" in printed
    session: Session = maker()
    row = session.execute(select(User).where(User.email == "ops@demo.test")).scalar_one()
    assert PASSWORD not in row.password_hash
    assert session_login(session, settings, email="ops@demo.test", password=PASSWORD)
    assert len(audit_actions(session, "user.create", org=DEMO_ORG)) == 1
    session.close()


def test_the_user_script_refuses_an_unknown_role(capsys):
    with pytest.raises(SystemExit):
        user_script.main(
            ["--email", "x@demo.test", "--role", "owner", "--org", DEMO_ORG],
            engine=None,
            read_password=lambda _p: PASSWORD,
        )
    assert "invalid choice" in capsys.readouterr().err


def test_load_and_reset_functions_are_what_the_scripts_call(engine):
    """The scripts delegate to `enggraph.demo`; the tests pin the delegation.

    Both functions run against the same doubles here as in the app, so a script cannot
    drift into its own write path.
    """
    Base.metadata.create_all(engine)
    repo = InMemoryGraphRepository()
    session: Session = build_sessionmaker(engine)()
    demo_load.run(repo, session, DEMO_ORG, replace=False)
    assert repo.counts(DEMO_ORG)["API"] == 4
    actions = select(func.count()).select_from(AuditRecord).where(AuditRecord.action == "demo.load")
    assert session.execute(actions).scalar_one() == 1
    demo_reset.run(repo, session, DEMO_ORG)
    assert repo.counts(DEMO_ORG) == {}
    actions = (
        select(func.count()).select_from(AuditRecord).where(AuditRecord.action == "demo.reset")
    )
    assert session.execute(actions).scalar_one() == 1
    session.close()
