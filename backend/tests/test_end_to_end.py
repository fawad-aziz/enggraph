"""End-to-end journey (milestone 8): ingestion, portal API, MCP — one graph.

Production code paths run here: the real sync worker over recorded Azure DevOps
payloads, the real FastAPI routers, the real MCP tools, the real authorization
code. The credential path is the production one — `production_client_factory`
resolves the PAT from the *reference* stored on the connector row, so no test
hands a raw PAT to a client. The only doubles are the two the suite always
uses: `InMemoryGraphRepository` (same semantics as the Neo4j repository) and
`httpx.MockTransport` (recorded payloads).

One module-scoped fixture builds one tenant and drives the whole journey:
sign-in and provisioning, connector configuration, two full passes, a failing
pass, an incremental pass, catalog writes, impact analysis through both
surfaces, and a second organization probing the same data. Every response, MCP
result, database row, and log record the journey produces is captured, so the
last tests can make claims about the journey as a whole rather than one call.

Not exercised here (documented in `docs/known-limitations.md`): a real Neo4j, a
real Postgres, a real Azure DevOps tenant, a real browser, a real Docker network.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import httpx
import pytest
from ado_fixtures import ORG, PAT, PROJECT, handler
from api_support import (
    ADMIN_EMAIL,
    CONNECTOR_ID,
    DEVELOPER_ID,
    OTHER_ADMIN_EMAIL,
    OTHER_ORG,
    PASSWORD,
    PROJECT_ID,
    REPO_ID,
    TEAM_ID,
    add_user,
    bearer,
    login,
)
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from mcp_support import (
    AUTHOR_DANA,
    AUTHOR_OMAR,
    COMMIT_ABC,
    MCP_TOOL_NAMES,
    PR_ID,
    WI_42,
    WI_43,
    build_server,
    call,
    context_for,
)
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from enggraph.config import Settings
from enggraph.connectors.errors import ConnectorConfigError
from enggraph.graph.in_memory import InMemoryGraphRepository
from enggraph.graph.model import NodeUpsert
from enggraph.main import create_app
from enggraph.persistence.engine import build_sessionmaker
from enggraph.persistence.models import (
    AuditRecord,
    AuthSession,
    Base,
    ConnectorConfig,
    ImpactAnalysis,
    JobEvent,
    OrganizationConfig,
    SyncCheckpoint,
    SyncJob,
    User,
)
from enggraph.services.impact import INFERRED_CONFIDENCE
from enggraph.sync.pipeline import get_checkpoint, scope_for
from enggraph.sync.worker import ClientFactory, production_client_factory, run_once

from tests.conftest import FakeDriver

PAT_REF = "AZURE_DEVOPS_PAT"
STALE_REPO_ID = "ado:acme:Commerce:repository:r-gone"
# Rows the credential scan reads: every table the journey writes.
SCANNED_MODELS = (
    User,
    AuthSession,
    OrganizationConfig,
    ConnectorConfig,
    SyncJob,
    JobEvent,
    SyncCheckpoint,
    ImpactAnalysis,
    AuditRecord,
)


class CaptureHandler(logging.Handler):
    """Raw records, captured before redaction runs on the output handler.

    `setup_logging()` clears the root handlers, so this handler is installed
    after the app and the MCP server are built — otherwise the journey's own
    logs would be filtered out before capture.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.getMessage(), str(record.__dict__)))


class Journey:
    """One tenant: one app, one graph, one MCP server, three principals."""

    def __init__(
        self,
        settings: Settings,
        engine: Any,
        repo: InMemoryGraphRepository,
        server: Any,
        client_factory: ClientFactory,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.repo = repo
        self.server = server
        self.client_factory = client_factory
        self.sessionmaker = build_sessionmaker(engine)
        self.client: TestClient
        self.admin_token = ""
        self.viewer_token = ""
        self.other_token = ""
        self.admin_id = ""
        self.viewer_id = ""
        self.last_full_job_id = ""
        self.ids: dict[str, str] = {}
        self.api_bodies: list[Any] = []
        self.mcp_results: list[Any] = []
        self.capture = CaptureHandler()

    def api(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A real request, recorded so the credential scan covers everything.

        `request()` is used for every verb: `httpx.Client.get()` has no `json`
        argument, so a verb-dispatched call cannot pass a body uniformly.
        """
        resp = self.client.request(
            method.upper(), path, headers=bearer(token), json=json_body, params=params
        )
        body = resp.json()
        self.api_bodies.append(body)
        return body

    def login(self, email: str) -> str:
        token = login(self.client, email)
        self.api("get", "/api/v1/auth/me", token)
        self.api_bodies.pop()  # the /me call is a probe, not part of the journey
        return token

    def sync(self, mode: str) -> dict[str, Any]:
        """Enqueue through the API, run the real worker, read the job back."""
        enqueued = self.api(
            "post",
            "/api/v1/sync-jobs",
            self.admin_token,
            json_body={"connectorConfigId": CONNECTOR_ID, "mode": mode},
        )
        job_id = enqueued["jobId"]
        session = self.sessionmaker()
        job = run_once(session, "worker-1", client_factory=self.client_factory, repo=self.repo)
        session.close()
        assert job is not None and job.id == job_id, "the worker must claim the enqueued job"
        result = self.api("get", f"/api/v1/sync-jobs/{job_id}", self.admin_token)
        if mode == "FULL" and result["state"] == "SUCCEEDED":
            # Reconciliation runs only on a complete pass, so the checkpoint's last
            # full pass is the most recent succeeded FULL job, not the first one.
            self.last_full_job_id = job_id
        return result

    def configure(self, project_names: list[str]) -> dict[str, Any]:
        return self.api(
            "post",
            "/api/v1/connectors/azure-devops/configure",
            self.admin_token,
            json_body={
                "adoOrganization": ORG,
                "patSecretRef": PAT_REF,
                "projectNames": project_names,
                "pageSize": 2,
            },
        )

    def checkpoint(self, project: str = PROJECT) -> SyncCheckpoint | None:
        session = self.sessionmaker()
        row = get_checkpoint(session, ORG, CONNECTOR_ID, scope_for(project))
        session.close()
        return row

    def counts(self) -> dict[str, int]:
        return dict(self.repo.counts(ORG))

    def rows(self, model: type) -> list[Any]:
        session = self.sessionmaker()
        rows = list(session.execute(select(model)).scalars().all())
        session.close()
        return rows

    async def mcp(self, tool: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        result = await call(self.server, tool, args, token=token)
        self.mcp_results.append(result)
        return result

    async def mcp_refusal(self, tool: str, args: dict[str, Any], token: str, code: str) -> None:
        """A refusal reaches the MCP caller as a tool error naming the same code.

        `MCPServer.call_tool` propagates the `ToolError` (the HTTP transport is what
        turns it into `isError: true`), so the assertion is on the raised error. The
        words are the API's, and no credential appears in them.
        """
        ctx = context_for(self.server, bearer(token))
        with pytest.raises(ToolError) as raised:
            await self.server.call_tool(tool, args, context=ctx)
        text = str(raised.value)
        assert code in text, text
        assert PAT not in text and PASSWORD not in text

    def haystacks(self) -> list[str]:
        """Every string the journey emitted: responses, MCP results, rows, logs."""
        texts = [json.dumps(body, default=str) for body in self.api_bodies]
        texts += [json.dumps(result, default=str) for result in self.mcp_results]
        texts += [str(row.__dict__) for model in SCANNED_MODELS for row in self.rows(model)]
        texts += [message + " " + payload for message, payload in self.capture.records]
        return texts


@pytest.fixture(scope="module")
def journey() -> Any:
    """One tenant, one app, one graph, one MCP server — built once for the journey.

    The fake PAT (`tests/ado_fixtures.py`) is placed in the process environment
    under the name the connector row references, which is how the API resolves it
    in production, and how `.env` supplies it to the worker. It is removed at
    teardown so no later test sees a credential-shaped value in the environment.
    """
    os.environ[PAT_REF] = PAT
    settings = Settings(
        mode="demo",
        postgres_dsn="sqlite://",
        neo4j_uri="bolt://test:7687",
        neo4j_user="neo4j",
        neo4j_password="testpw",
        session_secret="test-secret-32-bytes-long-aaaaaaa",
    ).model_copy(update={"azure_devops_pat": PAT})
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
    )
    transport = httpx.MockTransport(handler)
    Base.metadata.create_all(engine)
    session = build_sessionmaker(engine)()
    session.add(
        ConnectorConfig(
            id=CONNECTOR_ID,
            organization_id=ORG,
            ado_organization=ORG,
            pat_secret_ref=PAT_REF,
            project_names=[PROJECT],
            page_size=2,
        )
    )
    session.commit()
    session.close()

    repo = InMemoryGraphRepository()
    # The production client factory, so the PAT is resolved from its reference;
    # the recorded payloads are the transport it hands the client.
    factory = production_client_factory(settings, transport)
    app = create_app(
        settings=settings,
        engine=engine,
        driver=FakeDriver(),
        repo=repo,
        client_factory=factory,
        ado_transport=transport,
    )
    server = build_server(settings, engine, FakeDriver(), repo)

    trip = Journey(settings, engine, repo, server, factory)
    root = logging.getLogger()
    root.addHandler(trip.capture)
    root.setLevel(logging.DEBUG)
    add_user(engine, ADMIN_EMAIL, "admin", ORG)
    add_user(engine, OTHER_ADMIN_EMAIL, "admin", OTHER_ORG)

    with TestClient(app, raise_server_exceptions=False) as client:
        trip.client = client
        trip.admin_token = login(client, ADMIN_EMAIL)
        trip.admin_id = trip.api("get", "/api/v1/auth/me", trip.admin_token)["userId"]
        # Viewer provisioning goes through the API: admin-only, org-scoped.
        provisioned = trip.api(
            "post",
            "/api/v1/auth/users",
            trip.admin_token,
            json_body={"email": "viewer@acme.test", "password": PASSWORD, "role": "viewer"},
        )
        trip.viewer_id = provisioned["userId"]
        assert provisioned["organizationId"] == ORG
        trip.viewer_token = login(client, "viewer@acme.test")
        trip.other_token = login(client, OTHER_ADMIN_EMAIL)

        # Connector: validate, then configure (the reference, not the secret).
        validated = trip.api(
            "post",
            "/api/v1/connectors/azure-devops/validate",
            trip.admin_token,
            json_body={"adoOrganization": ORG, "patSecretRef": PAT_REF},
        )
        assert validated["reachable"] is True
        # configure() validates against the provider and writes the row, reusing the
        # one the fixture seeded for this organization.
        configured = trip.configure([PROJECT])
        assert configured["id"] == CONNECTOR_ID
        assert configured["lastValidationOk"] is True
        trip.first_full = trip.sync("FULL")

        # Catalog writes go through the same admin-gated endpoints the portal uses.
        checkout = trip.api(
            "post",
            "/api/v1/services",
            trip.admin_token,
            json_body={"name": "Checkout Service", "tier": "application"},
        )
        billing = trip.api(
            "post",
            "/api/v1/services",
            trip.admin_token,
            json_body={"name": "Billing Service", "tier": "application"},
        )
        orders_api = trip.api(
            "post",
            "/api/v1/apis",
            trip.admin_token,
            json_body={"name": "POST /orders", "method": "POST", "path": "/orders"},
        )
        for rel_type, src, dst in (
            ("OWNS", TEAM_ID, checkout["entityId"]),
            ("IMPLEMENTED_BY", checkout["entityId"], REPO_ID),
            ("EXPOSES", checkout["entityId"], orders_api["entityId"]),
            ("CONSUMES", billing["entityId"], orders_api["entityId"]),
            ("DEPENDS_ON", checkout["entityId"], billing["entityId"]),
        ):
            trip.api(
                "post",
                "/api/v1/relationships",
                trip.admin_token,
                json_body={"relType": rel_type, "srcId": src, "dstId": dst},
            )
        trip.ids = {
            "checkout": checkout["entityId"],
            "billing": billing["entityId"],
            "api": orders_api["entityId"],
        }
        yield trip

    root.removeHandler(trip.capture)
    os.environ.pop(PAT_REF, None)


def test_sign_in_and_provisioning_are_organization_scoped(journey: Any) -> None:
    admin = journey.api("get", "/api/v1/auth/me", journey.admin_token)
    viewer = journey.api("get", "/api/v1/auth/me", journey.viewer_token)
    assert (admin["role"], admin["organizationId"]) == ("admin", ORG)
    assert (viewer["role"], viewer["organizationId"]) == ("viewer", ORG)
    # A viewer cannot queue a job: it writes into the graph store.
    denied = journey.api(
        "post",
        "/api/v1/sync-jobs",
        journey.viewer_token,
        json_body={
            "connectorConfigId": CONNECTOR_ID,
            "mode": "FULL",
        },
    )
    assert denied["error"]["code"] == "admin_required"
    # No token at all is refused, not treated as anonymous.
    anonymous = journey.client.get("/api/v1/sync-jobs")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "missing_token"
    # A different organization sees no jobs of this one, at any privilege level.
    other_jobs = journey.api("get", "/api/v1/sync-jobs", journey.other_token, params={"limit": 100})
    assert other_jobs["total"] == 0


def test_the_connector_stores_a_reference_not_the_pat(journey: Any) -> None:
    connector = journey.rows(ConnectorConfig)[0]
    assert connector.pat_secret_ref == PAT_REF
    assert connector.ado_organization == ORG
    assert connector.project_names == [PROJECT]
    assert connector.last_validation_ok is True
    # The configure response and the stored row carry no PAT material.
    assert PAT not in json.dumps(journey.api_bodies, default=str)
    assert PAT not in str(connector.__dict__)
    audit = journey.rows(AuditRecord)
    configure = [row for row in audit if row.action == "connector.configure"]
    assert configure and configure[-1].details["secretReference"] == PAT_REF


def test_the_worker_resolves_the_pat_from_its_reference(journey: Any) -> None:
    connector = journey.rows(ConnectorConfig)[0]
    client = production_client_factory(journey.settings)(connector)
    assert str(client._http.base_url).startswith("https://dev.azure.com/")
    assert client._http.base_url.path.rstrip("/").endswith(ORG)
    expected = base64.b64encode(f":{PAT}".encode()).decode()
    assert client._http.headers["authorization"] == f"Basic {expected}"
    client.close()
    # A missing secret is a typed configuration failure, and its message is safe.
    blank = journey.settings.model_copy(update={"azure_devops_pat": ""})
    with pytest.raises(ConnectorConfigError) as raised:
        production_client_factory(blank)(connector)
    assert PAT_REF in str(raised.value)
    assert PAT not in str(raised.value)


def test_a_full_pass_enqueued_through_the_api_ingests_the_provider_graph(journey: Any) -> None:
    job = journey.first_full
    assert job["state"] == "SUCCEEDED"
    assert job["error"] is None
    assert job["projectsPlanned"] == 1 and job["projectsCompleted"] == 1
    assert job["nodesWritten"] > 0 and job["edgesWritten"] > 0
    assert job["finishedAt"] is not None
    stages = [event["stage"] for event in job["events"]]
    assert stages == ["start", "fetch:projects", "persist:Commerce", "reconcile", "complete"]
    # The organization endpoint reports the same counts the graph holds.
    orgs = journey.api("get", "/api/v1/organizations", journey.viewer_token)
    assert orgs["items"][0]["graphCounts"] == journey.counts()
    counts = journey.counts()
    assert counts["Repository"] == 1 and counts["Project"] == 1
    assert counts["Commit"] == 3 and counts["PullRequest"] == 1
    assert counts["WorkItem"] == 2 and counts["Developer"] == 4
    assert counts["Organization"] == 1 and counts["Team"] == 1


def test_the_portal_reads_the_ingested_graph_with_provider_provenance(journey: Any) -> None:
    token = journey.viewer_token
    hits = journey.api(
        "get",
        "/api/v1/search",
        token,
        params={"q": "commerce", "types": "Repository", "limit": 10, "offset": 0},
    )
    assert [item["entityId"] for item in hits["items"]] == [REPO_ID]
    detail = journey.api("get", f"/api/v1/entities/{REPO_ID}", token)
    assert detail["sourceSystem"] == "azure_devops"
    assert detail["sourceUrl"].startswith("https://dev.azure.com/")
    assert detail["isDemoData"] is False
    # Bounded neighborhood, read at two depths. The manual catalog edges the fixture
    # wrote (Team OWNS Checkout, Checkout IMPLEMENTED_BY this repository) put the team
    # two hops from the repository, so depth 1 does not reach it and depth 2 does:
    # manual edges participate in traversal exactly like provider edges, and the
    # depth the reader asked for is the depth the answer respects.
    at_one = journey.api(
        "get", f"/api/v1/entities/{REPO_ID}/neighborhood", token, params={"depth": 1, "limit": 50}
    )
    one = {node["entityId"] for node in at_one["nodes"]}
    assert REPO_ID in one  # the subject is part of the answer
    assert PROJECT_ID in one
    assert TEAM_ID not in one
    near = journey.api(
        "get", f"/api/v1/entities/{REPO_ID}/neighborhood", token, params={"depth": 2, "limit": 50}
    )
    ids = {node["entityId"] for node in near["nodes"]}
    assert PROJECT_ID in ids and "ado:azure:acme" in ids
    assert journey.ids["checkout"] in ids
    assert TEAM_ID in ids
    # Path highlighting honours the same bound: no witness at depth 1, and the depth-2
    # witness is built only from the manual catalog edges.
    shallow = journey.api(
        "get",
        f"/api/v1/entities/{REPO_ID}/paths",
        token,
        params={"to": TEAM_ID, "depth": 1, "limit": 20},
    )
    assert shallow["paths"] == []
    witness = journey.api(
        "get",
        f"/api/v1/entities/{REPO_ID}/paths",
        token,
        params={"to": TEAM_ID, "depth": 2, "limit": 20},
    )
    assert witness["paths"]
    used = {edge["relType"] for path in witness["paths"] for edge in path["relationships"]}
    assert used == {"IMPLEMENTED_BY", "OWNS"}
    assert TEAM_ID in {node["entityId"] for path in witness["paths"] for node in path["entities"]}


async def test_mcp_results_match_the_api_on_the_same_graph(journey: Any) -> None:
    token = journey.viewer_token
    api_hits = journey.api(
        "get",
        "/api/v1/search",
        token,
        params={"q": "commerce", "types": "Repository", "limit": 10, "offset": 0},
    )
    mcp_hits = await journey.mcp(
        "search_engineering_entities",
        {"query": "commerce", "types": ["Repository"], "limit": 10, "offset": 0},
        token,
    )
    assert mcp_hits["organizationId"] == ORG
    assert mcp_hits["requestedBy"] == "viewer@acme.test"
    assert [item["entityId"] for item in mcp_hits["items"]] == [
        item["entityId"] for item in api_hits["items"]
    ]
    assert mcp_hits["total"] == api_hits["total"]

    api_neighbors = journey.api(
        "get", f"/api/v1/entities/{REPO_ID}/neighborhood", token, params={"depth": 4, "limit": 50}
    )
    mcp_context = await journey.mcp("get_entity_context", {"entity_id": REPO_ID}, token)
    assert {node["entityId"] for node in mcp_context["entities"]} == {
        node["entityId"] for node in api_neighbors["nodes"]
    }

    repo_context = await journey.mcp("get_repository_context", {"repository_id": REPO_ID}, token)
    commit_ids = {entry["commit"]["entityId"] for entry in repo_context["commits"]}
    pr_ids = {entry["pullRequest"]["entityId"] for entry in repo_context["pullRequests"]}
    assert COMMIT_ABC in commit_ids and PR_ID in pr_ids
    work_item_ids = {entry["workItem"]["entityId"] for entry in repo_context["workItems"]}
    assert WI_42 in work_item_ids
    assert {entry["service"]["entityId"] for entry in repo_context["services"]} == {
        journey.ids["checkout"]
    }
    owner_teams = {entry["team"]["entityId"] for entry in repo_context["explicitOwners"]}
    assert owner_teams == {TEAM_ID}
    contributors = {
        entry["developer"]["entityId"] for entry in repo_context["inferredContributors"]
    }
    # Inferred contributors are labelled as review signals, not as owners.
    assert all(
        entry["label"].startswith("inferred") for entry in repo_context["inferredContributors"]
    )
    assert contributors <= {AUTHOR_DANA, AUTHOR_OMAR, DEVELOPER_ID, "ado:acme:identity:i-uuid-2"}
    assert {AUTHOR_DANA, AUTHOR_OMAR} <= contributors

    # Work items are reached only through relationship paths, and the MCP depth is
    # the same bound the API applies: this repository's two work items are two hops
    # away (PullRequest CHANGES / LINKED_TO), so depth 2 reaches both.
    work = await journey.mcp("get_related_work_items", {"entity_id": REPO_ID, "depth": 2}, token)
    reached = {entry["workItem"]["entityId"] for entry in work["workItems"]}
    assert reached == {WI_42, WI_43}
    assert work["relationshipAllowlist"] == [
        "CONTAINS_PULL_REQUEST",
        "CONTAINS_COMMIT",
        "CHANGES",
        "LINKED_TO",
        "PARENT_OF",
        "RELATED_TO",
    ]
    # The same depth-2 read through the API contains the same work items: the MCP
    # allowlist selects a subset of the edges the portal can traverse, and both
    # surfaces honour the same bound.
    api_items = {
        node["entityId"]
        for node in journey.api(
            "get",
            f"/api/v1/entities/{REPO_ID}/neighborhood",
            token,
            params={"depth": 2, "limit": 50},
        )["nodes"]
    }
    assert reached <= api_items
    # A type filter narrows the traversal, not just the answer. Filtering to
    # `WorkItem` stops at the subject, because the only route to a work item runs
    # through a pull request, which the filter excludes. The filtered read therefore
    # reports the subject alone — a bound to state, not a missing link.
    filtered = journey.api(
        "get",
        f"/api/v1/entities/{REPO_ID}/neighborhood",
        token,
        params={"depth": 2, "limit": 50, "node_types": "WorkItem"},
    )
    assert [node["entityId"] for node in filtered["nodes"]] == [REPO_ID]
    assert filtered["filters"]["nodeTypes"] == ["WorkItem"]

    # Dependency paths are the same edges the portal's relationship list shows.
    deps = await journey.mcp(
        "get_service_dependencies", {"service_id": journey.ids["checkout"], "depth": 1}, token
    )
    targets = {path["target"]["entityId"] for path in deps["paths"]}
    outgoing = {
        edge["dstId"]
        for edge in journey.api(
            "get",
            f"/api/v1/entities/{journey.ids['checkout']}/relationships",
            token,
            params={"limit": 100},
        )["items"]
        if edge["srcId"] == journey.ids["checkout"]
    }
    assert targets == outgoing
    assert deps["relationshipAllowlist"] == ["DEPENDS_ON", "CONSUMES", "EXPOSES", "IMPLEMENTED_BY"]


async def test_an_mcp_impact_report_is_reopenable_through_the_api(journey: Any) -> None:
    token = journey.viewer_token
    api_report = journey.api(
        "post",
        "/api/v1/impact-analysis",
        token,
        json_body={"subjectId": REPO_ID, "depth": 4, "limit": 50},
    )
    mcp_report = await journey.mcp(
        "get_change_impact", {"entity_id": REPO_ID, "depth": 4, "limit": 50}, token
    )
    assert mcp_report["channel"] == "mcp"
    for category in api_report["counts"]:
        assert sorted(
            f["entityId"] for f in api_report["findings"] if f["category"] == category
        ) == sorted(f["entityId"] for f in mcp_report["findings"] if f["category"] == category)
    # The documented inference rule is about services that share an API, so a
    # repository subject produces no inferred findings: nearness to a service is not
    # inference, and the report does not claim it is.
    assert api_report["counts"]["INFERRED"] == 0
    assert not [f for f in mcp_report["findings"] if f["category"] == "INFERRED"]
    assert all(f["category"] in mcp_report["counts"] for f in mcp_report["findings"])
    # From the service that exposes the API, Billing is reached by the cross-consumer
    # rule, and both surfaces say the same thing about it: the path is Checkout
    # EXPOSES the API, Billing CONSUMES it, at inference confidence, with the catalog
    # edges as its evidence.
    for report in (
        journey.api(
            "post",
            "/api/v1/impact-analysis",
            token,
            json_body={"subjectId": journey.ids["checkout"], "depth": 4, "limit": 50},
        ),
        await journey.mcp(
            "get_change_impact",
            {"entity_id": journey.ids["checkout"], "depth": 4, "limit": 50},
            token,
        ),
    ):
        inferred = [f for f in report["findings"] if f["category"] == "INFERRED"]
        assert [f["entityId"] for f in inferred] == [journey.ids["billing"]]
        assert [e["relType"] for e in inferred[0]["path"]["relationships"]] == [
            "EXPOSES",
            "CONSUMES",
        ]
        assert inferred[0]["confidence"] == INFERRED_CONFIDENCE
        assert inferred[0]["hasManualEvidence"] is True
    assert all(f["path"]["relationships"] for f in mcp_report["findings"])
    first_line = mcp_report["limitations"][0]
    assert "depth 4" in first_line and "50 entities" in first_line

    # The report the MCP tool persisted is readable by id through the API, in the
    # organization that produced it, naming the viewer as the requester.
    reopened = journey.api("get", f"/api/v1/impact-analysis/{mcp_report['analysisId']}", token)
    assert reopened["subjectId"] == REPO_ID
    assert reopened["requestedBy"] == journey.viewer_id
    # The stored report carries no channel marker — the envelope is the tool's, not
    # the persisted report's.
    assert reopened.get("channel") is None


async def test_the_mcp_surface_cannot_write_the_graph(journey: Any) -> None:
    token = journey.viewer_token
    before = (
        journey.counts(),
        journey.api("get", "/api/v1/services", token, params={"limit": 100})["total"],
        journey.api("get", "/api/v1/relationships", token, params={"limit": 100})["total"],
        journey.api("get", "/api/v1/sync-jobs", token, params={"limit": 100})["total"],
        len(journey.rows(ImpactAnalysis)),
    )
    args: dict[str, dict[str, Any]] = {
        "search_engineering_entities": {"query": "commerce"},
        "get_entity_context": {"entity_id": REPO_ID},
        "get_repository_context": {"repository_id": REPO_ID},
        "get_service_dependencies": {"service_id": journey.ids["checkout"]},
        "get_change_impact": {"entity_id": REPO_ID},
        "get_related_work_items": {"entity_id": REPO_ID},
        "get_component_owners": {"entity_id": REPO_ID},
    }
    for tool in MCP_TOOL_NAMES:
        await journey.mcp(tool, args[tool], token)
    after = (
        journey.counts(),
        journey.api("get", "/api/v1/services", token, params={"limit": 100})["total"],
        journey.api("get", "/api/v1/relationships", token, params={"limit": 100})["total"],
        journey.api("get", "/api/v1/sync-jobs", token, params={"limit": 100})["total"],
        len(journey.rows(ImpactAnalysis)),
    )
    assert after[:4] == before[:4], "the MCP surface must not write graph rows"
    # The documented non-graph write: analysis + audit records, which the tool names
    # in its caveats.
    assert after[4] > before[4]
    names = sorted(tool.name for tool in await journey.server.list_tools())
    assert names == sorted(MCP_TOOL_NAMES)
    assert "run_cypher" not in names


async def test_catalog_writes_are_admin_only_and_provider_edges_stay_sync_owned(
    journey: Any,
) -> None:
    viewer = journey.api(
        "post", "/api/v1/services", journey.viewer_token, json_body={"name": "Viewer Service"}
    )
    assert viewer["error"]["code"] == "admin_required"
    created = journey.api(
        "post",
        "/api/v1/services",
        journey.admin_token,
        json_body={"name": "Admin Service", "tier": "gateway"},
    )
    assert created["sourceSystem"] == "enggraph"
    assert created["isDemoData"] is False
    seen_by_viewer = journey.api(
        "get", "/api/v1/services", journey.viewer_token, params={"limit": 100}
    )
    assert created["entityId"] in [item["entityId"] for item in seen_by_viewer["items"]]
    # A provider-owned edge is not a catalog write, even for an admin.
    refused = journey.api(
        "delete",
        "/api/v1/relationships",
        journey.admin_token,
        params={"relType": "CONTAINS_REPOSITORY", "srcId": PROJECT_ID, "dstId": REPO_ID},
    )
    assert refused["error"]["code"] == "relationship_not_manual"
    assert (
        journey.api("get", f"/api/v1/entities/{REPO_ID}", journey.viewer_token)["entityId"]
        == REPO_ID
    )
    # Manual edges are the admin's to remove, and the removal is visible afterwards.
    deleted = journey.api(
        "delete",
        "/api/v1/relationships",
        journey.admin_token,
        params={"relType": "OWNS", "srcId": TEAM_ID, "dstId": journey.ids["checkout"]},
    )
    assert deleted["deleted"] is True
    remaining = journey.api(
        "get", "/api/v1/relationships", journey.admin_token, params={"limit": 100}
    )
    assert all((edge["relType"], edge["srcId"]) != ("OWNS", TEAM_ID) for edge in remaining["items"])
    assert remaining["total"] == len(remaining["items"])


def test_a_second_full_pass_is_idempotent_and_reconciles_stale_records(journey: Any) -> None:
    baseline = journey.counts()
    # A provider node the next pass will not see. `organizationId` is written in the
    # props (the repository keeps scoping in the node properties), so reconciliation
    # can find it.
    journey.repo.upsert_nodes(
        [
            NodeUpsert(
                label="Repository",
                id=STALE_REPO_ID,
                organization_id=ORG,
                props={
                    "organizationId": ORG,
                    "name": "gone-api",
                    "sourceSystem": "azure_devops",
                    "sourceId": "r-gone",
                    "sourceUrl": "https://dev.azure.com/acme/Commerce/_apis/git/repositories/r-gone",
                    "lastSeenAt": "2020-01-01T00:00:00Z",
                },
            )
        ]
    )
    assert journey.counts()["Repository"] == baseline["Repository"] + 1

    second = journey.sync("FULL")
    assert second["state"] == "SUCCEEDED"
    # Repeated sync writes no duplicate nodes: the counts return to the baseline.
    assert journey.counts() == baseline
    # Reconciliation marks records the pass did not see, and reports the count.
    reconcile = [event for event in second["events"] if event["stage"] == "reconcile"]
    assert reconcile and reconcile[0]["message"] == "marked_not_seen=1"
    stale = journey.repo.get_node(STALE_REPO_ID, ORG)
    assert stale is not None and stale.props["isDeleted"] is True
    # A marked node leaves the search index, so the portal stops showing it.
    assert (
        journey.api("get", "/api/v1/search", journey.viewer_token, params={"q": "gone-api"})[
            "total"
        ]
        == 0
    )
    checkpoint = journey.checkpoint()
    assert checkpoint is not None
    assert checkpoint.last_full_pass_job_id == second["jobId"]


def test_a_failed_pass_leaves_the_checkpoint_and_stays_diagnosable(journey: Any) -> None:
    before = journey.checkpoint()
    assert before is not None and before.provider_version == 0
    assert before.last_full_pass_job_id == journey.last_full_job_id
    # Mutating the same connector row (configure reuses it for the organization).
    mutated = journey.configure(["Ghost"])
    assert mutated["id"] == CONNECTOR_ID
    failed = journey.sync("FULL")
    assert failed["state"] == "FAILED"
    assert "Ghost" in (failed["error"] or "")
    assert failed["projectsCompleted"] == 0
    # The checkpoint is untouched: a failed page must not skip unprocessed data, and
    # reconciliation only runs on a complete pass.
    after = journey.checkpoint()
    assert after.provider_version == 0
    assert after.last_full_pass_job_id == journey.last_full_job_id
    assert all(event["stage"] != "reconcile" for event in failed["events"])
    assert any(event["level"] == "ERROR" for event in failed["events"])
    # Retry is a new queued job linked to the failed one, and the history is kept.
    retry = journey.api("post", f"/api/v1/sync-jobs/{failed['jobId']}/retry", journey.admin_token)
    assert retry["parentJobId"] == failed["jobId"]
    assert retry["state"] == "QUEUED"
    assert retry["attempt"] == failed["attempt"] + 1
    # Cancel it: a retried ghost project must not be claimed by a later pass.
    canceled = journey.api(
        "post", f"/api/v1/sync-jobs/{retry['jobId']}/cancel", journey.admin_token
    )
    assert canceled["state"] == "CANCELED"
    restored = journey.configure([PROJECT])
    assert restored["id"] == CONNECTOR_ID


def test_an_incremental_pass_advances_the_checkpoint_from_the_changes_api(journey: Any) -> None:
    incremental = journey.sync("INCREMENTAL")
    assert incremental["state"] == "SUCCEEDED"
    events = {event["stage"]: event["message"] for event in incremental["events"]}
    assert events[f"changes:{PROJECT}"] == "since_version=0"
    assert events[f"persist:{PROJECT}"].endswith("version=6")
    checkpoint = journey.checkpoint()
    assert checkpoint is not None
    assert checkpoint.provider_version == 6
    # A full pass is the reconciliation anchor; an incremental run does not claim it.
    assert checkpoint.last_full_pass_job_id == journey.last_full_job_id
    # The work items the changes API reported are readable under their canonical ids,
    # with the provider prefix (`ado:`) that keeps two providers' numbering apart.
    for canonical in (WI_42, WI_43):
        item = journey.api("get", f"/api/v1/entities/{canonical}", journey.viewer_token)
        assert item["sourceSystem"] == "azure_devops"
        assert item["isDemoData"] is False
    # The next run starts where the last one finished, so the provider position is
    # consumed once, not replayed.
    second = journey.sync("INCREMENTAL")
    stages = {event["stage"]: event["message"] for event in second["events"]}
    assert stages[f"changes:{PROJECT}"] == "since_version=6"
    assert journey.checkpoint().provider_version == 6


async def test_a_second_organization_cannot_see_acme_data(journey: Any) -> None:
    token = journey.other_token
    assert journey.api("get", "/api/v1/search", token, params={"q": "commerce"})["total"] == 0
    refused = journey.api("get", f"/api/v1/entities/{REPO_ID}", token)
    assert refused["error"]["code"] == "entity_not_found"
    assert journey.api("get", "/api/v1/sync-jobs", token, params={"limit": 100})["total"] == 0
    impact = journey.api(
        "post",
        "/api/v1/impact-analysis",
        token,
        json_body={"subjectId": journey.ids["checkout"]},
    )
    assert impact["error"]["code"] == "entity_not_found"
    orgs = journey.api("get", "/api/v1/organizations", token)
    assert orgs["items"][0]["organizationId"] == OTHER_ORG
    assert orgs["items"][0]["graphCounts"] == {}
    projects = journey.api("get", "/api/v1/connectors/azure-devops/projects", token)
    assert projects["error"]["code"] == "connector_not_configured"
    # The MCP surface refuses the same data with the same words.
    await journey.mcp_refusal(
        "get_entity_context", {"entity_id": REPO_ID}, token, "entity_not_found"
    )
    await journey.mcp_refusal(
        "get_change_impact", {"entity_id": journey.ids["checkout"]}, token, "entity_not_found"
    )
    other_search = await journey.mcp("search_engineering_entities", {"query": "commerce"}, token)
    assert other_search["total"] == 0
    assert other_search["organizationId"] == OTHER_ORG


def test_no_credential_appears_in_responses_rows_logs_or_mcp_results(journey: Any) -> None:
    texts = journey.haystacks()
    assert texts, "the journey produced material to scan"
    assert all(PAT not in text for text in texts)
    assert all(PASSWORD not in text for text in texts)
    # Authorization material never reaches a log line, raw or in extra fields.
    for message, payload in journey.capture.records:
        joined = (message + " " + payload).lower()
        assert "basic " not in joined
        assert "bearer " not in joined
    # Stored credentials are hashes, not the values that authenticate.
    sessions = journey.rows(AuthSession)
    assert sessions
    for row in sessions:
        assert len(row.token_hash) == 64
        assert all(char in "0123456789abcdef" for char in row.token_hash)
        assert row.token_hash != journey.admin_token
        assert row.token_hash != journey.viewer_token
    users = journey.rows(User)
    assert users
    for row in users:
        assert row.password_hash != PASSWORD
        assert PASSWORD not in row.password_hash


def test_the_audit_trail_names_the_actor_of_every_write(journey: Any) -> None:
    audit = journey.rows(AuditRecord)
    actions = {row.action for row in audit}
    assert {
        "connector.configure",
        "sync.enqueue",
        "sync.complete",
        "sync.reconcile",
        "catalog.service_create",
        "catalog.relationship_create",
        "impact.analysis",
        "sync.retry",
        "sync.cancel",
    } <= actions
    by_action = {row.action: row for row in audit}
    # Machine writes name the system; human writes name the user.
    assert by_action["sync.reconcile"].actor == "system"
    assert by_action["connector.configure"].actor == journey.admin_id
    assert by_action["catalog.service_create"].actor == journey.admin_id
    assert by_action["catalog.relationship_create"].actor == journey.admin_id
    assert by_action["impact.analysis"].actor == journey.viewer_id
    assert by_action["sync.retry"].actor == journey.admin_id
    assert by_action["sync.cancel"].actor == journey.admin_id
    # Every write names the record it wrote: a blank target is not an audit record.
    assert all(row.target.strip() for row in audit)
