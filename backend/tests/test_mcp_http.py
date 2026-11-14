"""MCP streamable-HTTP integration tests (milestone 7, FR-10).

The client is the MCP SDK's own streamable HTTP client, driven against the real
Starlette app `http_app_for` builds, over the ASGI transport (no network bind, no
uvicorn process). The graph is the one the real sync worker produced from the
recorded Azure DevOps fixtures, and authorization is the real session code.

What is pinned on the wire:
- the surface a client discovers is exactly the documented seven tools, annotated
  read-only, with no organization, credential, Cypher, or write parameter;
- an authenticated call returns structured JSON scoped to the caller's organization;
- no token, an invalid token, a revoked session, and a cross-organization request
  all arrive as `isError: true` with a readable `code: message`, not as a connection
  failure, and the session keeps working afterwards;
- the server's own process token is never inherited by an unauthenticated request;
- responses and access logs carry no credential;
- DNS-rebinding protection refuses an unexpected Host (421) and an unexpected
  browser Origin (403), for the localhost default, for a wider explicit bind, and
  for a container bind that declares the address its clients present;
- only the MCP endpoint is served, and a POST without a JSON content type is refused.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from ado_fixtures import ORG, PAT
from api_support import ADMIN_EMAIL, OTHER_ADMIN_EMAIL, VIEWER_EMAIL, build_app
from httpx2 import ASGITransport, AsyncClient
from mcp_support import (
    HTTP_BASE,
    MCP_TOOL_NAMES,
    REPO_ID,
    build_server,
    http_app,
    mcp_session,
    raw_post,
    serving,
    user_token,
)
from sqlalchemy.orm import Session, sessionmaker

from enggraph.auth.service import resolve_principal, revoke_session
from enggraph.graph.model import NodeUpsert
from enggraph.mcp import server as mcp_server_module
from enggraph.mcp.server import create_mcp_server
from enggraph.persistence.engine import build_engine
from enggraph.persistence.models import Base

MCP_TOKEN_ENV = "ENGGRAPH_MCP_TOKEN"
WIDER_HOST = "10.0.0.5"
WIDER_BASE = f"http://{WIDER_HOST}:8100"
EVIL_BASE = "http://evil.example:8100"


@pytest.fixture()
def http_server(settings, engine, fake_driver, monkeypatch):
    """App over the seeded fixture graph, with the stdio env token unset."""
    monkeypatch.delenv(MCP_TOKEN_ENV, raising=False)
    _app, repo = build_app(settings, engine, fake_driver, seed=True)
    server = build_server(settings, engine, fake_driver, repo)
    return http_app(server), repo


@pytest.fixture()
def tokens(settings, engine, http_server):
    return {
        "admin": user_token(settings, engine, ADMIN_EMAIL, "admin", ORG),
        "viewer": user_token(settings, engine, VIEWER_EMAIL, "viewer", ORG),
        "other": user_token(settings, engine, OTHER_ADMIN_EMAIL, "admin", "other"),
    }


# ------------------------------------------------------------------ what a client discovers


async def test_a_client_discovers_the_documented_read_only_tools(http_server):
    app, _repo = http_server
    async with serving(app), mcp_session(app) as session:
        assert session.server_info.name == "enggraph"
        assert "read-only" in session.instructions
        tools = (await session.list_tools()).tools
        assert sorted(tool.name for tool in tools) == sorted(MCP_TOOL_NAMES)
        for tool in tools:
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.idempotent_hint is True
            # A structured result schema is declared, so a client can validate it.
            assert tool.output_schema is not None
            params = (tool.input_schema or {}).get("properties", {})
            for name in params:
                # No organization selector, no credential, no query escape hatch.
                assert "organization" not in name.lower()
                assert not any(
                    word in name.lower() for word in ("pat", "token", "password", "secret")
                )
                assert not any(
                    word in name.lower() for word in ("cypher", "statement", "dsl", "sql")
                )
        assert "run_cypher" not in [tool.name for tool in tools]


async def test_an_authenticated_call_returns_structured_json(http_server, tokens):
    app, _repo = http_server
    async with serving(app), mcp_session(app, tokens["admin"]) as session:
        result = await session.call_tool(
            "search_engineering_entities", {"query": "commerce", "limit": 5}
        )
        assert not result.is_error
        body = result.structured_content
        assert body["organizationId"] == ORG
        assert body["requestedBy"] == ADMIN_EMAIL
        assert body["limit"] == 5
        assert body["generatedAt"].endswith("Z")
        assert any("bounded" in caveat for caveat in body["caveats"])
        for item in body["items"]:
            assert item["sourceUrl"].startswith("https://dev.azure.com/")


# ------------------------------------------------------------------ refusal on the wire


async def test_a_call_without_a_token_is_refused_on_the_wire(http_server):
    app, _repo = http_server
    async with serving(app), mcp_session(app) as session:
        result = await session.call_tool("search_engineering_entities", {"query": "commerce"})
        assert result.is_error is True
        assert "missing_token" in json.dumps(result.content, default=str)
        assert result.structured_content is None


async def test_an_invalid_token_is_refused_on_the_wire(http_server, tokens):
    app, _repo = http_server
    async with serving(app), mcp_session(app, tokens["viewer"] + "x") as session:
        result = await session.call_tool("search_engineering_entities", {"query": "commerce"})
        assert result.is_error is True
        assert "invalid_token" in json.dumps(result.content, default=str)


async def test_a_revoked_session_is_refused_on_the_wire(http_server, tokens, settings, engine):
    app, _repo = http_server
    maker = sessionmaker(engine, class_=Session, expire_on_commit=False)
    session_db = maker()
    principal = resolve_principal(session_db, settings, tokens["viewer"])
    assert principal is not None
    revoke_session(session_db, principal)
    session_db.close()
    async with serving(app), mcp_session(app, tokens["viewer"]) as session:
        result = await session.call_tool("search_engineering_entities", {"query": "commerce"})
        assert result.is_error is True
        assert "invalid_token" in json.dumps(result.content, default=str)


async def test_a_token_cannot_read_another_organization_over_http(http_server, tokens):
    """A node that exists only in the second organization is invisible to the first
    organization's token: the read is scoped by the principal, not by the argument."""
    app, repo = http_server
    isolated = "enggraph:other:service:isolated"
    repo.upsert_nodes(
        [
            NodeUpsert(
                label="Service",
                id=isolated,
                props={"name": "Isolated", "organizationId": "other", "sourceSystem": "enggraph"},
                organization_id="other",
            )
        ]
    )
    async with serving(app):
        async with mcp_session(app, tokens["other"]) as session:
            own = await session.call_tool("get_entity_context", {"entity_id": isolated})
            assert not own.is_error
            assert own.structured_content["organizationId"] == "other"
        async with mcp_session(app, tokens["viewer"]) as session:
            cross = await session.call_tool("get_entity_context", {"entity_id": isolated})
            assert cross.is_error is True
            assert "entity_not_found" in json.dumps(cross.content, default=str)
            assert cross.structured_content is None


async def test_a_refused_call_is_an_error_the_client_can_read_and_the_session_survives(
    http_server, tokens
):
    """`isError: true` with the reason in the content, not a broken connection: the
    same session serves the next call."""
    app, _repo = http_server
    async with serving(app), mcp_session(app, tokens["viewer"]) as session:
        bad = await session.call_tool("get_entity_context", {"entity_id": "does-not-exist"})
        assert bad.is_error is True
        text = json.dumps(bad.content, default=str)
        assert "entity_not_found" in text
        # No traceback, no server internals in what the client reads.
        assert "Traceback" not in text
        good = await session.call_tool("get_entity_context", {"entity_id": REPO_ID})
        assert not good.is_error
        assert good.structured_content["subject"]["entityId"] == REPO_ID


async def test_the_process_token_is_never_inherited_by_an_http_request(
    http_server, tokens, monkeypatch
):
    """The stdio env token is not a fallback for HTTP: an unauthenticated request on a
    server that holds a token must be refused, not served as that identity."""
    app, _repo = http_server
    monkeypatch.setenv(MCP_TOKEN_ENV, tokens["admin"])
    async with serving(app), mcp_session(app) as session:
        result = await session.call_tool("search_engineering_entities", {"query": "commerce"})
        assert result.is_error is True
        assert "missing_token" in json.dumps(result.content, default=str)


async def test_responses_never_carry_a_credential_over_http(http_server, tokens, settings):
    app, _repo = http_server
    payloads = []
    async with serving(app), mcp_session(app, tokens["admin"]) as session:
        for name, args in [
            ("search_engineering_entities", {"query": "commerce"}),
            ("get_entity_context", {"entity_id": REPO_ID}),
        ]:
            payloads.append((await session.call_tool(name, args)).structured_content)
    text = json.dumps(payloads, default=str)
    # The PAT seeded the graph, the session secret minted the caller's token, the
    # Neo4j password is the store credential: none may appear in a response.
    assert PAT not in text
    assert settings.session_secret not in text
    assert settings.neo4j_password not in text
    assert tokens["admin"] not in text


async def test_http_access_logs_name_the_caller_and_never_log_the_token(
    http_server, tokens, settings, caplog
):
    app, _repo = http_server
    with caplog.at_level(logging.INFO, logger="enggraph.mcp"):
        async with serving(app), mcp_session(app, tokens["viewer"]) as session:
            await session.call_tool(
                "search_engineering_entities", {"query": "commerce", "limit": 5}
            )
    records = [record for record in caplog.records if getattr(record, "tool", None)]
    assert records, "every tool call over HTTP is logged"
    record = records[0]
    assert record.tool == "search_engineering_entities"
    assert record.organization_id == ORG
    assert record.limit == 5
    logged = str(record.__dict__)
    assert tokens["viewer"] not in logged
    assert PAT not in logged
    assert settings.session_secret not in logged


async def test_a_server_with_no_graph_store_names_the_missing_store(
    tmp_path, settings, monkeypatch
):
    """Neo4j unreachable at startup: the server stays up, and a tool names the store it
    cannot reach instead of crashing inside a query on `None`."""
    dsn = f"sqlite:///{(tmp_path / 'mcp.db').as_posix()}"
    store_settings = settings.model_copy(update={"postgres_dsn": dsn})
    store_engine = build_engine(dsn)
    Base.metadata.create_all(store_engine)
    token = user_token(store_settings, store_engine, ADMIN_EMAIL, "admin", ORG)
    monkeypatch.delenv(MCP_TOKEN_ENV, raising=False)
    monkeypatch.setattr(mcp_server_module, "open_driver", lambda uri, user, password: None)
    app = http_app(create_mcp_server(store_settings), "127.0.0.1")
    async with serving(app), mcp_session(app, token) as session:
        result = await session.call_tool("search_engineering_entities", {"query": "commerce"})
        assert result.is_error is True
        text = json.dumps(result.content, default=str)
        assert "graph_unavailable" in text
        assert token not in text


# ------------------------------------------------------------------ transport security


async def test_an_unexpected_host_is_refused(http_server):
    """DNS-rebinding protection: the bound host must appear in `Host`, port included."""
    app, _repo = http_server
    async with serving(app):
        assert (await raw_post(app, HTTP_BASE)).status_code == 200
        assert (await raw_post(app, EVIL_BASE)).status_code == 421


async def test_an_unexpected_browser_origin_is_refused(http_server):
    app, _repo = http_server
    async with serving(app):
        assert (await raw_post(app, HTTP_BASE, origin="http://evil.example")).status_code == 403
        assert (await raw_post(app, HTTP_BASE, origin="http://127.0.0.1:8100")).status_code == 200


async def test_a_wider_bind_declares_the_host_and_origin_it_accepts(settings, engine, fake_driver):
    """A non-localhost bind carries explicit allowed host/origin, so it is reachable
    only through a proxy presenting exactly that host."""
    _app, repo = build_app(settings, engine, fake_driver, seed=True)
    server = build_server(settings, engine, fake_driver, repo)
    app = http_app(server, WIDER_HOST)
    async with serving(app):
        assert (await raw_post(app, WIDER_BASE)).status_code == 200
        assert (await raw_post(app, EVIL_BASE)).status_code == 421
        assert (
            await raw_post(app, WIDER_BASE, origin="http://attacker.example:8100")
        ).status_code == 403
        assert (
            await raw_post(app, WIDER_BASE, origin=f"http://{WIDER_HOST}:8100")
        ).status_code == 200


async def test_a_container_bind_declares_the_address_its_clients_present(
    settings, engine, fake_driver
):
    """A container publishes a loopback port while the process binds every interface,
    so the address a client presents is not the bind address. Declaring it keeps the
    client's `Host` accepted and an unrelated `Host` refused."""
    _app, repo = build_app(settings, engine, fake_driver, seed=True)
    server = build_server(settings, engine, fake_driver, repo)
    app = http_app(
        server,
        "0.0.0.0",
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:8100"],
    )
    async with serving(app):
        assert (await raw_post(app, HTTP_BASE)).status_code == 200
        assert (await raw_post(app, HTTP_BASE, origin="http://127.0.0.1:8100")).status_code == 200
        assert (await raw_post(app, EVIL_BASE)).status_code == 421
        assert (await raw_post(app, HTTP_BASE, origin="http://evil.example")).status_code == 403


async def test_only_the_mcp_endpoint_is_served(http_server):
    """The MCP app exposes its endpoint and nothing else: the REST API, the health
    route, and the graph stores are not reachable through it."""
    app, _repo = http_server
    async with (
        serving(app),
        AsyncClient(transport=ASGITransport(app=app), base_url=HTTP_BASE) as client,
    ):
        for path in ("/", "/health", "/api/v1/entities", "/sse", "/messages/"):
            assert (await client.get(path)).status_code == 404
        for path in ("/", "/health", "/api/v1/entities"):
            assert (await raw_post(app, HTTP_BASE, path=path)).status_code == 404
        # The MCP endpoint is a session-scoped stream, not a plain HTTP resource:
        # a GET asks for the server-initiated event stream and stays open.
        stream = asyncio.create_task(client.get("/mcp"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(stream, 3)
        stream.cancel()


async def test_a_post_without_a_json_content_type_is_refused(http_server):
    app, _repo = http_server
    async with serving(app):
        response = await raw_post(app, HTTP_BASE, content_type="text/plain")
        assert response.status_code == 400
