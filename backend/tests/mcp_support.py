"""Shared MCP test support (milestone 7).

MCP tests run the real tool code path: the same domain services the REST API
uses, against the graph the real sync worker produced from the recorded Azure
DevOps fixtures, with authorization resolved by the real session code.
Test doubles are the same two the API tests inject — `InMemoryGraphRepository`
and SQLite — so no Neo4j/Postgres container is needed.

`context_for` builds the request context exactly the streamable HTTP transport
builds it (a request object carrying headers), so header-borne bearer tokens are
exercised the way a real client sends them.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from api_support import ADMIN_EMAIL, PASSWORD, REPO_ID, TEAM_ID
from httpx2 import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from enggraph.auth.service import create_user, login, resolve_principal
from enggraph.config import Settings
from enggraph.graph.repository import GraphRepository
from enggraph.mcp.server import create_mcp_server, http_app_for
from enggraph.services.catalog import create_api, create_relationship, create_service

OTHER_ORG = "other"
MCP_TOOL_NAMES = [
    "search_engineering_entities",
    "get_entity_context",
    "get_repository_context",
    "get_service_dependencies",
    "get_change_impact",
    "get_related_work_items",
    "get_component_owners",
]

# Canonical ids outside api_support (the same ids the API tests assert on).
PR_ID = "ado:acme:Commerce:pullrequest:r-uuid-1:7"
WI_42 = "ado:acme:Commerce:workitem:42"
WI_43 = "ado:acme:Commerce:workitem:43"
COMMIT_ABC = "ado:acme:Commerce:commit:r-uuid-1:abc123"
# Commit/PR authors from the recorded fixtures (uniqueName-based canonical ids).
AUTHOR_DANA = "ado:acme:identity:u.dana@acme.com"
AUTHOR_OMAR = "ado:acme:identity:u.omar@acme.com"


def build_server(
    settings: Settings, engine: Engine, driver: Any, repo: GraphRepository
) -> MCPServer[None]:
    """Server with the test stores injected (no lifespan needed)."""
    return create_mcp_server(settings, engine, driver, repo)


def user_token(settings: Settings, engine: Engine, email: str, role: str, org: str) -> str:
    """Create the user, then mint a session token through the auth service."""
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    create_user(
        session, email=email, password=PASSWORD, role=role, organization_id=org, actor="seed"
    )
    token = login(session, settings, email=email, password=PASSWORD)
    session.close()
    assert token is not None, "login must mint a session token"
    return token


def bearer(token: str, casing: str = "Authorization") -> dict[str, str]:
    return {casing: f"Bearer {token}"}


def context_for(server: MCPServer[None], headers: dict[str, str] | None) -> Context:
    """Request context carrying headers, as the HTTP transport hands it to a tool."""
    request = SimpleNamespace(headers=headers) if headers is not None else None
    request_context = ServerRequestContext(
        session=None,
        lifespan_context=None,
        protocol_version="2025-06-18",
        method="tools/call",
        params=None,
        request_id=1,
        request=request,
    )
    return Context(request_context=request_context, mcp_server=server, subscriptions=None)


async def call(
    server: MCPServer[None], tool: str, args: dict[str, Any], *, token: str | None = None
) -> dict[str, Any]:
    """Call a tool and return its structured content (the JSON a client receives)."""
    ctx = context_for(server, bearer(token) if token else None)
    result = await server.call_tool(tool, args, context=ctx)
    assert not result.is_error, result
    return result.structured_content  # type: ignore[no-any-return]


# The bound host must appear in the Host header with its port: localhost DNS-rebinding
# protection allows "127.0.0.1:*", so a client addressing port 80 is refused.
HTTP_BASE = "http://127.0.0.1:8100"
MCP_URL = f"{HTTP_BASE}/mcp"

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "1"},
    },
}


def http_app(
    server: MCPServer[None],
    host: str = "127.0.0.1",
    *,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> Any:
    """Streamable HTTP app for a bind host. Stateless JSON responses keep assertions readable."""
    return http_app_for(
        server,
        host,
        stateless_http=True,
        json_response=True,
        allowed_hosts=allowed_hosts or [],
        allowed_origins=allowed_origins or [],
    )


def serving(app: Any) -> Any:
    """Run the app's lifespan. The SDK's session manager allows one run per app
    instance, so a test that opens several sessions enters this once and keeps it open."""
    return app.router.lifespan_context(app)


@asynccontextmanager
async def mcp_session(app: Any, token: str | None = None, base: str = HTTP_BASE) -> Any:
    """The SDK's own streamable HTTP client over the ASGI app (no network bind).

    The app must already be serving (inside `serving(app)`).
    """
    headers = bearer(token) if token else {}
    client = AsyncClient(transport=ASGITransport(app=app), base_url=base, headers=headers)
    async with (
        client,
        streamable_http_client(f"{base}/mcp", http_client=client) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        yield session


async def raw_post(
    app: Any,
    base: str,
    *,
    origin: str | None = None,
    path: str = "/mcp",
    body: Any = INITIALIZE_BODY,
    content_type: str = "application/json",
    accept: str = "application/json",
) -> Any:
    """A raw POST, with headers exactly as a client sends them.

    The body is sent as bytes so the `Content-Type` below is what reaches the server:
    passing `json=` lets the client overwrite the header with its own value.
    """
    request_headers = {"content-type": content_type, "accept": accept}
    if origin is not None:
        request_headers["origin"] = origin
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base) as client:
        return await client.post(path, content=json.dumps(body).encode(), headers=request_headers)


def configure_catalog(settings: Settings, engine: Engine, repo: GraphRepository) -> dict[str, str]:
    """Services, an API, and the catalog links, written through the real catalog
    service (the same code path the API endpoints use), by the seeded admin."""
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session = maker()
    token = login(session, settings, email=ADMIN_EMAIL, password=PASSWORD)
    principal = resolve_principal(session, settings, token)
    assert principal is not None
    checkout = create_service(session, repo, principal, name="Checkout Service", tier="application")
    billing = create_service(session, repo, principal, name="Billing Service", tier="application")
    orders_api = create_api(
        session, repo, principal, name="POST /orders", method="POST", path="/orders"
    )
    create_relationship(
        session, repo, principal, rel_type="OWNS", src_id=TEAM_ID, dst_id=checkout["entityId"]
    )
    create_relationship(
        session,
        repo,
        principal,
        rel_type="IMPLEMENTED_BY",
        src_id=checkout["entityId"],
        dst_id=REPO_ID,
    )
    create_relationship(
        session,
        repo,
        principal,
        rel_type="EXPOSES",
        src_id=checkout["entityId"],
        dst_id=orders_api["entityId"],
    )
    create_relationship(
        session,
        repo,
        principal,
        rel_type="CONSUMES",
        src_id=billing["entityId"],
        dst_id=orders_api["entityId"],
    )
    create_relationship(
        session,
        repo,
        principal,
        rel_type="DEPENDS_ON",
        src_id=checkout["entityId"],
        dst_id=billing["entityId"],
    )
    session.close()
    return {
        "checkout": checkout["entityId"],
        "billing": billing["entityId"],
        "api": orders_api["entityId"],
    }
