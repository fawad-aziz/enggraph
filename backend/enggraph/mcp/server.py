"""Secured read-only MCP tools (FR-10, milestone 7).

The MCP surface is deliberately thin: every tool delegates to the same domain
services the REST API uses, so there is no second implementation of business
logic and no path that skips the authorization the API applies.

Rules enforced here:

- Every tool requires an authenticated session. The bearer token is the same
  credential the API issues (`POST /api/v1/auth/login`), resolved by the same
  `resolve_principal`. The organization a caller may read comes from the
  principal only — it is never an argument.
- Every tool is read-only. There is no write tool, no `run_cypher`, and no
  user-supplied Cypher: traversals go through reviewed repository methods with
  fixed relationship allowlists, bounded depth, and bounded limits.
- Arguments are validated (ids, types, depth, limit) and clamped to the server's
  configured ceilings; the bounds actually used are echoed in the result.
- Results are structured JSON carrying source URLs, provenance types,
  timestamps, `truncated` flags, and a caveats list. Nothing credential-shaped
  is returned, logged, or accepted from a tool argument.

Transports:
- Streamable HTTP: `Authorization: Bearer <token>` per request, identical to the API.
- stdio: the client launches this process, so there is no per-request header;
  the operator supplies `ENGGRAPH_MCP_TOKEN` in the server process environment.
  `docs/mcp-setup.md` documents both.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import os
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from neo4j import Driver
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal, resolve_principal
from enggraph.config import Settings, load_settings, validate_runtime
from enggraph.graph.driver import open_driver
from enggraph.graph.model import RELATION_SEMANTICS, EdgeView, NodeView, PathView
from enggraph.graph.neo4j_repo import Neo4jGraphRepository
from enggraph.graph.repository import GraphRepository
from enggraph.logging_setup import get_logger, setup_logging
from enggraph.main import APP_VERSION
from enggraph.persistence.engine import build_engine, build_sessionmaker
from enggraph.services.display import edge_dto, node_dto, path_dto
from enggraph.services.entities import entity_neighborhood, get_entity
from enggraph.services.impact import INFERRED_CONFIDENCE, analyze_impact, validate_categories
from enggraph.services.search import search_entities

log = get_logger("enggraph.mcp")

EXIT_OK = 0
EXIT_CONFIG = 2

# Session token for stdio (no per-request headers there). Never a value in code:
# the operator supplies it in the server process environment.
MCP_TOKEN_ENV = "ENGGRAPH_MCP_TOKEN"
LOCALHOSTS = ("127.0.0.1", "localhost", "::1")

# Bounds the MCP contract declares for its own arguments. The server's configured
# ceilings (ENGGRAPH_MAX_DEPTH / ENGGRAPH_MAX_RESULTS) are applied on top.
DEFAULT_DEPTH = 4
DEFAULT_WORK_DEPTH = 2
DEFAULT_OWNERS_DEPTH = 2
DEFAULT_LIMIT = 50
MAX_ARG_DEPTH = 10
MAX_ARG_LIMIT = 200
MAX_ID_LENGTH = 200

# Fixed relationship allowlists per tool (no unrestricted traversal).
# Work items are reached through the containment hops that connect a repository
# to the pull requests and commits the work items are linked to, plus the work
# item hierarchy itself.
WORK_REL_TYPES: tuple[str, ...] = (
    "CONTAINS_PULL_REQUEST",
    "CONTAINS_COMMIT",
    "CHANGES",
    "LINKED_TO",
    "PARENT_OF",
    "RELATED_TO",
)
DEPENDENCY_REL_TYPES: tuple[str, ...] = ("DEPENDS_ON", "CONSUMES", "EXPOSES", "IMPLEMENTED_BY")
OWNERSHIP_REL_TYPES: tuple[str, ...] = ("OWNS", "IMPLEMENTED_BY")
CONTRIBUTOR_REL_TYPES: tuple[str, ...] = (
    "AUTHORED",
    "CREATED_BY",
    "REVIEWED_BY",
    "HAS_MEMBER",
    "CONTAINS_COMMIT",
    "CONTAINS_PULL_REQUEST",
)
# Provenance that can back an "explicit" ownership claim. INFERRED never can.
EXPLICIT_PROVENANCE = ("PROVIDER_EXPLICIT", "MANUAL", "STATIC_ANALYSIS", "RUNTIME_OBSERVED")

# Human-readable relationship semantics, so a tool result explains itself.
SEMANTIC_NOTES: dict[str, str] = {
    "HAS_PROJECT": "organization contains the project",
    "HAS_TEAM": "organization contains the team",
    "HAS_MEMBER": "team has the developer as a member",
    "CONTAINS_REPOSITORY": "project contains the repository",
    "AUTHORED": "developer authored the commit",
    "CONTAINS_COMMIT": "repository contains the commit",
    "CONTAINS_PULL_REQUEST": "repository contains the pull request",
    "CREATED_BY": "pull request was created by the developer",
    "REVIEWED_BY": "pull request was reviewed by the developer",
    "LINKED_TO": "pull request is linked to the work item",
    "CHANGES": "pull request changes the commit",
    "PARENT_OF": "work item is the parent of a child work item",
    "RELATED_TO": "work item is related to a peer work item",
    "OWNS": "team owns the service (configured in the catalog)",
    "IMPLEMENTED_BY": "service is implemented by the repository",
    "EXPOSES": "service exposes the API",
    "CONSUMES": "service consumes the API",
    "DEPENDS_ON": "service depends on another service",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_only() -> ToolAnnotations:
    """Every MCP tool is read-only, non-destructive and idempotent."""
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def _semantics(rel_type: str) -> dict[str, str]:
    src, dst = RELATION_SEMANTICS[rel_type]
    return {
        "relType": rel_type,
        "sourceLabel": src,
        "targetLabel": dst,
        "meaning": SEMANTIC_NOTES[rel_type],
    }


def _distinct_provenances(values: Iterable[Any]) -> list[str]:
    """Sorted provenance types present, skipping edges that carry none."""
    return sorted({value for value in values if isinstance(value, str)})


def _provenances(edges: list[dict[str, Any]]) -> list[str]:
    return _distinct_provenances(edge.get("provenanceType") for edge in edges)


def _source_urls(nodes: list[dict[str, Any]]) -> list[str]:
    urls = {node["sourceUrl"] for node in nodes if node.get("sourceUrl")}
    return sorted(urls)


def _caveats(depth: int | None, limit: int, notes: Iterable[str]) -> list[str]:
    """Caveats required in every result: bounds, provenance, freshness, scope."""
    bound = (
        f"Traversal was bounded to depth {depth} and {limit} results."
        if depth is not None
        else f"Results were bounded to {limit} items."
    )
    lines = [
        f"{bound} An empty result means 'not found within the bound', not 'does not exist'.",
        "Every relationship carries a provenance type; INFERRED edges are signals for "
        "human review, not verified facts.",
        "The graph reflects the last successful synchronization (node lastSeenAt); changes "
        "made in Azure DevOps after that are not visible here.",
        "Services, APIs, and manual links exist only where they have been configured in "
        "the catalog; absence means unconfigured, not absent.",
        "Source-system access control is not mirrored: everyone authorized in EngGraph "
        "reads the same graph for their organization.",
    ]
    lines.extend(notes)
    return lines


def _limit(settings: Settings, limit: int) -> tuple[int, list[str]]:
    if not 1 <= limit <= MAX_ARG_LIMIT:
        raise ToolError(f"invalid_request: limit must be 1..{MAX_ARG_LIMIT}")
    used = min(limit, settings.max_results)
    notes = []
    if used < limit:
        notes.append(f"the limit was clamped to the configured ceiling ({settings.max_results}).")
    return used, notes


def _bounds(settings: Settings, depth: int, limit: int) -> tuple[int, int, list[str]]:
    if not 1 <= depth <= MAX_ARG_DEPTH:
        raise ToolError(f"invalid_request: depth must be 1..{MAX_ARG_DEPTH}")
    used_limit, notes = _limit(settings, limit)
    used_depth = min(depth, settings.max_depth)
    if used_depth < depth:
        notes.append(f"the depth was clamped to the configured ceiling ({settings.max_depth}).")
    return used_depth, used_limit, notes


def _clean_id(value: str, field: str) -> str:
    """Canonical graph ids are compact, whitespace-free strings."""
    cleaned = value.strip()
    if not cleaned:
        raise ToolError(f"invalid_request: {field} must not be empty")
    if len(cleaned) > MAX_ID_LENGTH:
        raise ToolError(f"invalid_request: {field} is longer than {MAX_ID_LENGTH} characters")
    if any(char.isspace() for char in cleaned):
        raise ToolError(f"invalid_request: {field} must not contain whitespace")
    return cleaned


def _domain_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a domain service, turning API errors into MCP tool errors.

    `ToolError` becomes `isError: true` with the message in the result content, so
    the caller reads the same `code: message` words the API uses.
    """
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        raise ToolError(f"{exc.code}: {exc.message}") from exc


def _require_label(
    repo: GraphRepository, node_id: str, org_id: str, labels: tuple[str, ...], tool: str
) -> NodeView:
    """Organization-scoped lookup with an explicit label requirement."""
    node = repo.get_node(node_id, org_id)
    if node is None:
        raise ApiError("entity_not_found", f"entity {node_id} not found", 404)
    if node.label not in labels:
        raise ApiError(
            "invalid_entity_type",
            f"{tool} accepts a {'/'.join(labels)} node; {node_id} is a {node.label}",
            400,
        )
    return node


def _linked(
    repo: GraphRepository, org_id: str, from_id: str, rel_types: list[str], limit: int
) -> list[tuple[NodeView, EdgeView]]:
    """Target node + edge for every outgoing relationship of the listed types."""
    pairs: list[tuple[NodeView, EdgeView]] = []
    for edge in repo.outgoing(from_id, org_id, rel_types, limit):
        node = repo.get_node(edge.dst, org_id)
        if node is not None:
            pairs.append((node, edge))
    return pairs


def _arriving(
    repo: GraphRepository, org_id: str, to_id: str, rel_types: list[str], limit: int
) -> list[tuple[NodeView, EdgeView]]:
    """Source node + edge for every incoming relationship of the listed types."""
    pairs: list[tuple[NodeView, EdgeView]] = []
    for edge in repo.incoming(to_id, org_id, rel_types, limit):
        node = repo.get_node(edge.src, org_id)
        if node is not None:
            pairs.append((node, edge))
    return pairs


def _advances(path: PathView, direction: str) -> bool:
    """True when every edge on the path runs in the requested direction.

    Traversal engines walk adjacency in both directions, so direction is asserted
    on the path witness itself: for `out` each edge must leave the current node,
    for `in` each edge must arrive at it.
    """
    for index, edge in enumerate(path.edges):
        current = path.nodes[index].id
        next_id = path.nodes[index + 1].id
        if direction == "out" and not (edge.src == current and edge.dst == next_id):
            return False
        if direction == "in" and not (edge.dst == current and edge.src == next_id):
            return False
    return True


def _path_entry(path: PathView, field: str, target: NodeView) -> dict[str, Any]:
    """One bounded path: what it reaches, the relationship types it used, and the
    witness (nodes + edges with provenance, timestamps, source links)."""
    return {
        field: node_dto(target),
        "hopCount": len(path.edges),
        "relTypes": [edge.rel_type for edge in path.edges],
        "relationshipSemantics": [_semantics(edge.rel_type) for edge in path.edges],
        "provenanceTypes": _distinct_provenances(
            edge.props.get("provenanceType") for edge in path.edges
        ),
        "path": path_dto(path.nodes, path.edges),
    }


def _dependency_paths(
    repo: GraphRepository, org_id: str, node_id: str, direction: str, depth: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    raw = repo.expand_paths(node_id, org_id, list(DEPENDENCY_REL_TYPES), depth, limit + 1)
    kept = [path for path in raw if _advances(path, direction)]
    # The repository caps paths at the limit it is given (one slot beyond ours), so
    # a full `limit` paths means the traversal consumed its whole budget: this is
    # what makes truncation observable rather than inferred.
    truncated = len(raw) >= limit
    return [_path_entry(path, "target", path.nodes[-1]) for path in kept[:limit]], truncated


def _work_item_paths(
    repo: GraphRepository, org_id: str, node_id: str, depth: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    raw = repo.expand_paths(node_id, org_id, list(WORK_REL_TYPES), depth, limit + 1)
    items = [
        _path_entry(path, "workItem", path.nodes[-1])
        for path in raw
        if path.nodes[-1].label == "WorkItem" and path.nodes[-1].id != node_id
    ]
    return items[:limit], len(raw) >= limit


def _owner_groups(
    repo: GraphRepository, org_id: str, node_id: str, depth: int, limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Explicit ownership (configured OWNS edges) and separately labelled
    inferred contributors (authorship / review / membership history)."""
    ownership = repo.expand_paths(node_id, org_id, list(OWNERSHIP_REL_TYPES), depth, limit + 1)
    explicit: list[dict[str, Any]] = []
    for path in ownership:
        owns_edges = [edge for edge in path.edges if edge.rel_type == "OWNS"]
        if path.nodes[-1].label != "Team" or not owns_edges:
            continue
        provenances = _distinct_provenances(edge.props.get("provenanceType") for edge in owns_edges)
        if not set(provenances) <= set(EXPLICIT_PROVENANCE):
            continue
        entry = _path_entry(path, "team", path.nodes[-1])
        entry["label"] = "explicit (configured ownership)"
        explicit.append(entry)

    contributions = repo.expand_paths(
        node_id, org_id, list(CONTRIBUTOR_REL_TYPES), depth, limit + 1
    )
    inferred: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for path in contributions:
        developer = path.nodes[-1]
        if developer.label != "Developer" or developer.id == node_id:
            continue
        key = (developer.id, tuple(edge.rel_type for edge in path.edges))
        if key in seen:
            continue
        seen.add(key)
        entry = _path_entry(path, "developer", developer)
        entry["confidence"] = INFERRED_CONFIDENCE
        entry["label"] = "inferred (human review required)"
        inferred.append(entry)
    truncated = len(ownership) >= limit or len(contributions) >= limit
    return explicit[:limit], inferred[:limit], truncated


def _repository_context(
    repo: GraphRepository, org_id: str, repository_id: str, limit: int
) -> tuple[dict[str, Any], bool]:
    """Repository metadata, its commits and pull requests, work items reached
    through those pull requests, services that implement it, explicit owners for
    those services, and inferred contributors from authorship history."""
    repository = _require_label(
        repo, repository_id, org_id, ("Repository",), "get_repository_context"
    )
    commits = _linked(repo, org_id, repository.id, ["CONTAINS_COMMIT"], limit)
    pull_requests = _linked(repo, org_id, repository.id, ["CONTAINS_PULL_REQUEST"], limit)

    work_items: list[dict[str, Any]] = []
    for pr, pr_edge in pull_requests:
        for item, link_edge in _linked(repo, org_id, pr.id, ["LINKED_TO"], limit):
            work_items.append(
                {
                    "workItem": node_dto(item),
                    "viaPullRequest": node_dto(pr),
                    "path": path_dto([repository, pr, item], [pr_edge, link_edge]),
                }
            )

    services = _arriving(repo, org_id, repository.id, ["IMPLEMENTED_BY"], limit)
    explicit_owners: list[dict[str, Any]] = []
    for service, implements_edge in services:
        for team, owns_edge in _arriving(repo, org_id, service.id, ["OWNS"], limit):
            explicit_owners.append(
                {
                    "team": node_dto(team),
                    "viaService": node_dto(service),
                    "provenanceType": owns_edge.props.get("provenanceType"),
                    "label": "explicit (configured ownership)",
                    "path": path_dto([team, service, repository], [owns_edge, implements_edge]),
                }
            )

    contributors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for commit, contains_edge in commits:
        for developer, authored_edge in _arriving(repo, org_id, commit.id, ["AUTHORED"], limit):
            key = (developer.id, authored_edge.rel_type)
            if key in seen:
                continue
            seen.add(key)
            contributors.append(
                {
                    "developer": node_dto(developer),
                    "viaCommit": node_dto(commit),
                    "confidence": INFERRED_CONFIDENCE,
                    "label": "inferred (human review required)",
                    "path": path_dto(
                        [developer, commit, repository], [authored_edge, contains_edge]
                    ),
                }
            )
    for pr, pr_edge in pull_requests:
        for developer, rel_edge in _arriving(
            repo, org_id, pr.id, ["CREATED_BY", "REVIEWED_BY"], limit
        ):
            key = (developer.id, rel_edge.rel_type)
            if key in seen:
                continue
            seen.add(key)
            contributors.append(
                {
                    "developer": node_dto(developer),
                    "viaPullRequest": node_dto(pr),
                    "confidence": INFERRED_CONFIDENCE,
                    "label": "inferred (human review required)",
                    "path": path_dto([developer, pr, repository], [rel_edge, pr_edge]),
                }
            )

    truncated = any(len(group) >= limit for group in (commits, pull_requests, work_items, services))
    payload = {
        "repository": node_dto(repository),
        "commits": [{"commit": node_dto(node), "edge": edge_dto(edge)} for node, edge in commits],
        "pullRequests": [
            {"pullRequest": node_dto(node), "edge": edge_dto(edge)} for node, edge in pull_requests
        ],
        "workItems": work_items,
        "services": [
            {"service": node_dto(node), "edge": edge_dto(edge)} for node, edge in services
        ],
        "explicitOwners": explicit_owners,
        "inferredContributors": contributors,
    }
    return payload, truncated


class McpRuntime:
    """Data stores the tools read through (settings, Postgres sessions, graph store)."""

    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        sessionmaker: sessionmaker[Session],
        repo: GraphRepository | None,
        driver: Driver | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.repo = repo
        self.driver = driver

    def require_repo(self) -> GraphRepository:
        """The graph store, refused with the same message the REST API gives (503).

        Neo4j can be unreachable at startup: the server stays up, and a tool must
        name the missing store rather than fail inside a query on `None`.
        """
        if self.repo is None:
            raise ToolError("graph_unavailable: graph store is not connected")
        return self.repo


class ServerState:
    """Per-server holder: tools read the stores the running server is connected to."""

    def __init__(self) -> None:
        self.runtime: McpRuntime | None = None

    def require(self) -> McpRuntime:
        """Honest failure when the server was built but never started."""
        if self.runtime is None:
            raise ToolError(
                "server_not_started: the MCP server is not connected to Postgres and Neo4j"
            )
        return self.runtime


def _bearer_from(ctx: Context | None) -> str | None:
    """Session token from the request header (HTTP) or the operator env (stdio).

    The env token is the stdio fallback only. A request that arrives over HTTP with
    no `Authorization` header is refused: it must never be served with the identity
    the server process holds, because that turns a shared server into an open one.
    """
    env_token = os.environ.get(MCP_TOKEN_ENV, "").strip()
    if ctx is None:
        return env_token or None
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        # stdio: the client launched this process, so the operator supplies the token.
        return env_token or None
    headers = {
        key.lower(): value for key, value in dict(getattr(request, "headers", None) or {}).items()
    }
    header = headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[6:].strip() or None
    return None


def _principal_for(runtime: McpRuntime, ctx: Context | None) -> Principal:
    """Same session resolution and messages as the REST API (deps.get_principal)."""
    token = _bearer_from(ctx)
    if not token:
        raise ToolError("missing_token: authentication required")
    with runtime.sessionmaker() as session:
        principal = resolve_principal(session, runtime.settings, token)
    if principal is None:
        raise ToolError("invalid_token: session is invalid or expired")
    return principal


def _log_call(
    tool: str, principal: Principal, depth: int | None, limit: int, results: int, truncated: bool
) -> None:
    """Structured access log. No tokens, no PAT, no passwords (redaction filter applies)."""
    log.info(
        "mcp tool",
        extra={
            "tool": tool,
            "organization_id": principal.organization_id,
            "user_id": principal.user_id,
            "depth": depth,
            "limit": limit,
            "results": results,
            "truncated": truncated,
        },
    )


def create_mcp_server(
    settings: Settings | None = None,
    engine: Engine | None = None,
    driver: Driver | None = None,
    repo: GraphRepository | None = None,
) -> MCPServer[None]:
    """MCP server exposing the seven read-only EngGraph tools.

    `engine` and `repo` are injected by tests (SQLite + the in-memory graph engine,
    the same doubles the API tests use); production builds them from settings inside
    the lifespan. The read-only tool set is defined here and never extended at
    runtime: `MCPServer` only exposes tools that are registered in this factory.
    """
    settings = settings or load_settings()
    setup_logging()
    state = ServerState()
    if engine is not None and repo is not None:
        # Test entry point: stores are injected, so the server is ready without a lifespan.
        state.runtime = McpRuntime(settings, engine, build_sessionmaker(engine), repo, driver)

    @contextlib.asynccontextmanager
    async def lifespan(server: MCPServer[None]) -> AsyncIterator[None]:
        if state.runtime is not None:
            # Stores were injected at construction (tests): the server runs on them
            # and opens nothing. Production reaches the code below.
            yield
            return
        engine_in = engine if engine is not None else build_engine(settings.postgres_dsn)
        sess = build_sessionmaker(engine_in)
        drv = (
            driver
            if driver is not None
            else open_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        )
        state.runtime = McpRuntime(
            settings,
            engine_in,
            sess,
            Neo4jGraphRepository(drv) if drv is not None else None,
            drv,
        )
        yield
        state.runtime = None
        if drv is not None:
            drv.close()

    server = MCPServer(
        name="enggraph",
        title="EngGraph",
        description="Read-only engineering-graph context for coding assistants.",
        version=APP_VERSION,
        instructions=(
            "Every tool reads the EngGraph engineering graph for the caller's organization. "
            "Authentication uses the session bearer token the EngGraph API issues, resolved by "
            "the same code path: send it as `Authorization: Bearer <token>` for HTTP, or set "
            f"{MCP_TOKEN_ENV} in the server process environment for stdio. The organization a "
            "tool reads is taken from the token, never from an argument. Tools are read-only, "
            "bounded by depth and limit, and report `truncated` when a bound was reached — read "
            "`caveats` before quoting results. Relationship provenance is labelled; INFERRED "
            "edges require human review. There is no graph write tool and no way to run "
            "arbitrary Cypher."
        ),
        lifespan=lifespan,
    )

    @server.tool(
        name="search_engineering_entities",
        title="Search engineering entities",
        description=(
            "Search the caller's organization by name/title text, optionally filtered by entity "
            "types, with a bounded limit and offset. Read-only. Returns canonical entities with "
            "source URLs, provenance-bearing timestamps, and demo flags, plus `total` and "
            "`truncated` so a bounded answer is never mistaken for a complete one."
        ),
        annotations=_read_only(),
    )
    async def search_engineering_entities(
        ctx: Context,
        query: str,
        types: tuple[str, ...] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_limit, notes = _limit(runtime.settings, limit)
        if offset < 0:
            raise ToolError("invalid_request: offset must be 0 or greater")
        repo = runtime.require_repo()
        result = await asyncio.to_thread(
            _domain_call,
            search_entities,
            repo,
            query,
            principal.organization_id,
            list(types),
            used_limit,
            offset,
        )
        items = result["items"]
        truncated = result["total"] > offset + len(items)
        _log_call("search_engineering_entities", principal, None, used_limit, len(items), truncated)
        return {
            "organizationId": principal.organization_id,
            "requestedBy": principal.email,
            "query": result["query"],
            "types": result["types"],
            "items": items,
            "total": result["total"],
            "limit": result["limit"],
            "offset": result["offset"],
            "truncated": truncated,
            "generatedAt": _now_iso(),
            "caveats": _caveats(None, result["limit"], notes),
        }

    @server.tool(
        name="get_entity_context",
        title="Entity context",
        description=(
            "Bounded neighborhood of one entity inside the caller's organization: the entity "
            "plus the entities and relationships reachable within depth and limit, filtered by "
            "optional entity and relationship types. Read-only. Reports the bounds used, "
            "relationship semantics, provenance types, source URLs, and `truncated`."
        ),
        annotations=_read_only(),
    )
    async def get_entity_context(
        ctx: Context,
        entity_id: str,
        rel_types: tuple[str, ...] = (),
        node_types: tuple[str, ...] = (),
        depth: int = DEFAULT_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_depth, used_limit, notes = _bounds(runtime.settings, depth, limit)
        subject = _clean_id(entity_id, "entity_id")
        repo = runtime.require_repo()
        payload = await asyncio.to_thread(
            _domain_call,
            entity_neighborhood,
            repo,
            subject,
            principal.organization_id,
            list(node_types),
            list(rel_types),
            used_depth,
            used_limit,
        )
        nodes = payload["nodes"]
        edges = payload["edges"]
        truncated = payload["truncated"]
        _log_call("get_entity_context", principal, used_depth, used_limit, len(nodes), truncated)
        rel_present = sorted({edge["relType"] for edge in edges})
        return {
            "organizationId": principal.organization_id,
            "requestedBy": principal.email,
            "subject": payload["subject"],
            "entities": nodes,
            "relationships": edges,
            "depth": payload["depth"],
            "limit": payload["limit"],
            "filters": payload["filters"],
            "relationshipSemantics": [_semantics(rel_type) for rel_type in rel_present],
            "provenanceTypes": _provenances(edges),
            "sourceUrls": _source_urls(nodes),
            "truncated": truncated,
            "generatedAt": _now_iso(),
            "caveats": _caveats(payload["depth"], payload["limit"], notes),
        }

    @server.tool(
        name="get_repository_context",
        title="Repository context",
        description=(
            "One repository inside the caller's organization: metadata and source link, its "
            "commits and pull requests, the work items linked through those pull requests, the "
            "services that implement it, the teams that explicitly own those services, and "
            "inferred contributors from authorship history. Read-only. `limit` applies per "
            "group; `truncated` reports whether any group hit the bound."
        ),
        annotations=_read_only(),
    )
    async def get_repository_context(
        ctx: Context,
        repository_id: str,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_limit, notes = _limit(runtime.settings, limit)
        identifier = _clean_id(repository_id, "repository_id")
        repo = runtime.require_repo()
        payload, truncated = await asyncio.to_thread(
            _domain_call,
            _repository_context,
            repo,
            principal.organization_id,
            identifier,
            used_limit,
        )
        results = sum(
            len(payload[key]) for key in ("commits", "pullRequests", "workItems", "services")
        )
        _log_call("get_repository_context", principal, None, used_limit, results, truncated)
        return {
            "organizationId": principal.organization_id,
            "requestedBy": principal.email,
            **payload,
            "limit": used_limit,
            "truncated": truncated,
            "generatedAt": _now_iso(),
            "caveats": _caveats(
                None,
                used_limit,
                [
                    *notes,
                    "`limit` is applied to each group (commits, pull requests, work items, "
                    "services, owners, contributors), not to the total item count.",
                    "Path witnesses show stored edge direction (source → target), not traversal "
                    "order.",
                ],
            ),
        }

    @server.tool(
        name="get_service_dependencies",
        title="Service dependencies",
        description=(
            "Dependency paths for a service (or API) inside the caller's organization, in the "
            "requested direction: `out` = what this node depends on / consumes / exposes, `in` = "
            "what depends on it. Bounded depth and limit; every returned path is checked to "
            "advance strictly in the requested direction, and each edge carries its semantics, "
            "provenance and timestamps. Read-only. API-mediated consumers of a service are "
            "reported by get_change_impact (cross-consumer rule), not here."
        ),
        annotations=_read_only(),
    )
    async def get_service_dependencies(
        ctx: Context,
        service_id: str,
        direction: Literal["out", "in"] = "out",
        depth: int = DEFAULT_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_depth, used_limit, notes = _bounds(runtime.settings, depth, limit)
        identifier = _clean_id(service_id, "service_id")
        repo = runtime.require_repo()
        node = await asyncio.to_thread(
            _domain_call,
            _require_label,
            repo,
            identifier,
            principal.organization_id,
            ("Service", "API"),
            "get_service_dependencies",
        )
        paths, truncated = await asyncio.to_thread(
            _dependency_paths,
            repo,
            principal.organization_id,
            identifier,
            direction,
            used_depth,
            used_limit,
        )
        _log_call(
            "get_service_dependencies", principal, used_depth, used_limit, len(paths), truncated
        )
        return {
            "organizationId": principal.organization_id,
            "requestedBy": principal.email,
            "subject": node_dto(node),
            "direction": direction,
            "relationshipAllowlist": list(DEPENDENCY_REL_TYPES),
            "paths": paths,
            "depth": used_depth,
            "limit": used_limit,
            "truncated": truncated,
            "generatedAt": _now_iso(),
            "caveats": _caveats(
                used_depth,
                used_limit,
                [
                    *notes,
                    "Paths are filtered to strict direction: an edge that runs the other way is "
                    "not part of any result, so an empty list means 'no dependency path in that "
                    "direction within the bound'.",
                ],
            ),
        }

    @server.tool(
        name="get_change_impact",
        title="Change impact",
        description=(
            "Explainable change-impact analysis for one entity inside the caller's organization, "
            "grouped by category (DIRECT, DEPENDENCY, HISTORICAL, OWNERSHIP, INFERRED). Every "
            "finding carries the relationship path that explains its inclusion, confidence, "
            "provenance and source links, and the report includes a limitations section naming "
            "the bounds in effect. Read-only with respect to the graph: running it persists an "
            "analysis record (audit trail) in Postgres for the organization, and never writes "
            "graph nodes or relationships."
        ),
        annotations=_read_only(),
    )
    async def get_change_impact(
        ctx: Context,
        entity_id: str,
        categories: tuple[str, ...] = (),
        depth: int = DEFAULT_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_depth, used_limit, notes = _bounds(runtime.settings, depth, limit)
        subject = _clean_id(entity_id, "entity_id")
        repo = runtime.require_repo()
        cats = await asyncio.to_thread(_domain_call, validate_categories, list(categories))
        with runtime.sessionmaker() as session:
            report = await asyncio.to_thread(
                _domain_call,
                analyze_impact,
                session,
                repo,
                principal,
                subject_id=subject,
                categories=cats,
                depth=used_depth,
                limit=used_limit,
                max_depth=runtime.settings.max_depth,
                max_results=runtime.settings.max_results,
            )
        counts = report["counts"]
        total = sum(counts.values())
        truncated = report["truncated"]
        _log_call("get_change_impact", principal, used_depth, used_limit, total, truncated)
        result = dict(report)
        result["channel"] = "mcp"
        result["generatedAt"] = _now_iso()
        result["caveats"] = [
            *report["limitations"],
            *notes,
            "This tool persisted the report (analysisId) as an audit record for the "
            "organization; the graph itself was not modified.",
        ]
        return result

    @server.tool(
        name="get_related_work_items",
        title="Related work items",
        description=(
            "Work items reachable from one entity through LINKED_TO, CHANGES, PARENT_OF and "
            "RELATED_TO paths inside the caller's organization, bounded by depth and limit. "
            "Every item carries the path that reached it, the relationship types used, "
            "provenance types, source links, and provider timestamps. Read-only."
        ),
        annotations=_read_only(),
    )
    async def get_related_work_items(
        ctx: Context,
        entity_id: str,
        depth: int = DEFAULT_WORK_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_depth, used_limit, notes = _bounds(runtime.settings, depth, limit)
        subject = _clean_id(entity_id, "entity_id")
        repo = runtime.require_repo()
        node = await asyncio.to_thread(
            _domain_call, get_entity, repo, subject, principal.organization_id
        )
        items, truncated = await asyncio.to_thread(
            _work_item_paths,
            repo,
            principal.organization_id,
            subject,
            used_depth,
            used_limit,
        )
        _log_call(
            "get_related_work_items", principal, used_depth, used_limit, len(items), truncated
        )
        return {
            "organizationId": principal.organization_id,
            "requestedBy": principal.email,
            "subject": node,
            "relationshipAllowlist": list(WORK_REL_TYPES),
            "workItems": items,
            "depth": used_depth,
            "limit": used_limit,
            "truncated": truncated,
            "generatedAt": _now_iso(),
            "caveats": _caveats(
                used_depth,
                used_limit,
                [
                    *notes,
                    "Work items are matched only through relationship paths; proximity alone is "
                    "never treated as a link.",
                    "Path witnesses show stored edge direction (source → target), not traversal "
                    "order.",
                ],
            ),
        }

    @server.tool(
        name="get_component_owners",
        title="Component owners",
        description=(
            "Ownership for one component (service, repository, API, team, project) inside the "
            "caller's organization, in two separately labelled groups: `explicitOwners` are teams "
            "on configured OWNS relationships (catalog ownership, provenance-labelled), and "
            "`inferredContributors` are developers reached through authorship, review and "
            "membership history, reported with fixed confidence and always labelled as "
            "needing human review. Read-only; an unconfigured component reports no owners rather "
            "than a guess."
        ),
        annotations=_read_only(),
    )
    async def get_component_owners(
        ctx: Context,
        entity_id: str,
        depth: int = DEFAULT_OWNERS_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        runtime = state.require()
        principal = await asyncio.to_thread(_principal_for, runtime, ctx)
        used_depth, used_limit, notes = _bounds(runtime.settings, depth, limit)
        subject = _clean_id(entity_id, "entity_id")
        repo = runtime.require_repo()
        node = await asyncio.to_thread(
            _domain_call, get_entity, repo, subject, principal.organization_id
        )
        explicit, inferred, truncated = await asyncio.to_thread(
            _owner_groups, repo, principal.organization_id, subject, used_depth, used_limit
        )
        _log_call(
            "get_component_owners",
            principal,
            used_depth,
            used_limit,
            len(explicit) + len(inferred),
            truncated,
        )
        return {
            "organizationId": principal.organization_id,
            "requestedBy": principal.email,
            "component": node,
            "explicitOwners": explicit,
            "inferredContributors": inferred,
            "relationshipAllowlists": {
                "explicitOwners": list(OWNERSHIP_REL_TYPES),
                "inferredContributors": list(CONTRIBUTOR_REL_TYPES),
            },
            "depth": used_depth,
            "limit": used_limit,
            "truncated": truncated,
            "generatedAt": _now_iso(),
            "caveats": _caveats(
                used_depth,
                used_limit,
                [
                    *notes,
                    "Explicit ownership exists only where someone configured it (OWNS edges in "
                    "the catalog); an empty explicit list means ownership is unconfigured, not "
                    "that nobody owns the component.",
                    f"Inferred contributors use fixed confidence {INFERRED_CONFIDENCE} from "
                    "authorship, review and membership history. They are review signals, not "
                    "accountability.",
                ],
            ),
        }

    return server


def build_runtime_server() -> MCPServer[None]:
    """Entry point for the CLI: settings come from the environment."""
    return create_mcp_server()


def run_stdio() -> None:
    """Serve over stdio (the MCP client launches this process)."""
    asyncio.run(build_runtime_server().run_stdio_async())


def _pattern_list(value: str) -> list[str]:
    """Comma-separated settings value → list of transport patterns."""
    return [item.strip() for item in value.split(",") if item.strip()]


def http_app_for(
    server: MCPServer[None],
    bound_host: str,
    *,
    stateless_http: bool = False,
    json_response: bool = False,
    allowed_hosts: Iterable[str] = (),
    allowed_origins: Iterable[str] = (),
) -> Any:
    """Streamable HTTP app for a given bind host.

    DNS-rebinding protection is enabled for localhost binds by the SDK. A wider bind
    declares its allowed host/origin explicitly: a request whose `Host` is not the
    bound host is refused (421), and a browser `Origin` that is not the bound host
    is refused (403). A wider bind belongs behind a TLS-terminating reverse proxy
    that presents exactly this host.

    `allowed_hosts` / `allowed_origins` cover the case where the bind address is not
    the address clients present — a container publishes a loopback port while the
    process binds all interfaces, a proxy presents a public name. Patterns are
    matched against the request's `Host` (port included) and `Origin`.
    """
    hosts = [host for host in allowed_hosts if host.strip()]
    origins = [origin for origin in allowed_origins if origin.strip()]
    security: TransportSecuritySettings | None = None
    if bound_host not in LOCALHOSTS or hosts or origins:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts or [f"{bound_host}:*"],
            allowed_origins=origins or [f"http://{bound_host}:*", f"https://{bound_host}:*"],
        )
    return server.streamable_http_app(
        host=bound_host,
        transport_security=security,
        stateless_http=stateless_http,
        json_response=json_response,
    )


def run_http(host: str | None = None, port: int | None = None) -> None:
    """Serve streamable HTTP. Binds to localhost by default."""
    import uvicorn

    settings = load_settings()
    bound_host = host or settings.mcp_host
    server = create_mcp_server(settings)
    app = http_app_for(
        server,
        bound_host,
        allowed_hosts=_pattern_list(settings.mcp_allowed_hosts),
        allowed_origins=_pattern_list(settings.mcp_allowed_origins),
    )
    uvicorn.run(app, host=bound_host, port=port or settings.mcp_port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m enggraph.mcp",
        description="Run the EngGraph read-only MCP server.",
    )
    sub = parser.add_subparsers(dest="transport", required=True)
    sub.add_parser("stdio", help="serve over stdio (the client launches this process)")
    http = sub.add_parser("http", help="serve streamable HTTP")
    http.add_argument("--host", default=None, help="bind host (default MCP_HOST, localhost)")
    http.add_argument("--port", type=int, default=None, help="bind port (default MCP_PORT)")
    args = parser.parse_args(argv)
    setup_logging()

    settings = load_settings()
    problems = validate_runtime(settings)
    for problem in problems:
        log.error("configuration problem: %s", problem)
    if problems:
        return EXIT_CONFIG

    if args.transport == "stdio":
        if not os.environ.get(MCP_TOKEN_ENV):
            log.warning("%s is not set: stdio tool calls will be refused", MCP_TOKEN_ENV)
        run_stdio()
        return EXIT_OK
    run_http(args.host, args.port)
    return EXIT_OK
