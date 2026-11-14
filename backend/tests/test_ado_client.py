"""Contract tests for the Azure DevOps REST client (httpx.MockTransport).

These exercise real httpx request/response semantics — pagination, 429 with
Retry-After, transient 5xx/transport retries, and typed auth/permission
errors — against recorded payload fixtures. No fake connector stand-ins.
"""

from __future__ import annotations

import base64
import json
import logging
import pathlib

import httpx
import pytest

from enggraph.connectors.azure_devops.client import API_VERSION, MAX_COLLECTION_PAGES, AdoClient
from enggraph.connectors.azure_devops.connector import fetch_projects, validate_connection
from enggraph.connectors.errors import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorError,
    ConnectorPermissionError,
    ConnectorThrottled,
    ConnectorUnavailable,
)
from enggraph.logging_setup import redact

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ado"
PAT = "pat-abc123def456"


def load(name: str) -> dict:
    if not name.endswith(".json"):
        name = f"{name}.json"
    return json.loads((FIXTURES / name).read_text())


def make_client(handler, sleeps=None, **kwargs) -> AdoClient:
    return AdoClient(
        "acme",
        PAT,
        sleep=(lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_api_version_injected_and_pat_basic_auth():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=load("projects_p1"))

    client = make_client(handler)
    client.request("/_apis/projects")
    params = dict(seen[0].url.params)
    assert params["api-version"] == API_VERSION
    expected = base64.b64encode(f":{PAT}".encode()).decode()
    assert seen[0].headers["authorization"] == f"Basic {expected}"


def test_continuation_token_pagination_consumes_all_pages():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("continuationToken") == "token2":
            return httpx.Response(200, json=load("projects_p2"))
        return httpx.Response(200, json=load("projects_p1"))

    client = make_client(handler, sleeps=[])
    items = list(client.iter_collection("/_apis/projects", {"$top": 2}))
    names = [item["name"] for item in items]
    assert names == ["Commerce", "Data", "Ops"]
    assert len(seen) == 2  # page 2 has no @nextLink -> stop, no extra call


def test_skip_paging_stops_on_short_page():
    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("searchCriteria[skip]", "0"))
        body = load("commits_p1") if skip == 0 else load("commits_p2")
        return httpx.Response(200, json=body)

    client = make_client(handler)
    path = "/Commerce/_apis/git/repositories/r-uuid-1/commits"
    shas = [c["commitId"] for c in client.iter_skip_paged(path, {}, page_size=2)]
    assert shas == ["abc123", "def456", "fff777"]


def test_429_honours_retry_after_then_succeeds():
    sleeps: list[float] = []
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=load("repos.json"))

    client = make_client(handler, sleeps)
    items = list(client.iter_collection("/Commerce/_apis/git/repositories", {}))
    assert [r["name"] for r in items] == ["commerce-api"]
    assert sleeps == [2.0]
    assert state["calls"] == 2


def test_429_past_retry_budget_raises_throttled():
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"})

    client = make_client(handler, sleeps, max_retries=2)
    with pytest.raises(ConnectorThrottled):
        client.request("/_apis/projects")
    assert sleeps == [1.0, 1.0]


def test_transient_5xx_is_retried_then_succeeds():
    sleeps: list[float] = []
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] < 2:
            return httpx.Response(503)
        return httpx.Response(200, json=load("teams.json"))

    client = make_client(handler, sleeps)
    body = client.request("/Commerce/_apis/projects/Commerce/teams")
    assert body["count"] == 1
    assert len(sleeps) == 1  # exponential backoff applied once


def test_persistent_5xx_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = make_client(lambda r: handler(r), [], max_retries=2)
    with pytest.raises(ConnectorUnavailable):
        client.request("/_apis/projects")


def test_transport_error_retries_then_succeeds():
    sleeps: list[float] = []
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, json=load("teams.json"))

    client = make_client(handler, sleeps)
    body = client.request("/Commerce/_apis/projects/Commerce/teams")
    assert body["count"] == 1


def test_401_raises_auth_error_without_retry():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401)

    client = make_client(handler, [], max_retries=5)
    with pytest.raises(ConnectorAuthError):
        client.request("/_apis/projects")
    assert len(seen) == 1  # auth failures are never retried


def test_403_raises_permission_error_without_retry():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(403)

    client = make_client(handler, [], max_retries=5)
    with pytest.raises(ConnectorPermissionError):
        client.request("/Commerce/_apis/git/repositories")
    assert len(seen) == 1


def test_empty_pat_is_config_error():
    with pytest.raises(ConnectorConfigError):
        AdoClient("acme", "   ")


def test_validate_connection_reports_first_page_only():
    """Validation is a credential/reachability check: exactly one request.

    A handler that serves page 1 and then fails proves no continuation request
    is made, so validation never scales with organization size.
    """
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] > 1:
            raise AssertionError("validation must not page past the first page")
        return httpx.Response(200, json=load("projects_p1"))

    report = validate_connection("acme", PAT, transport=httpx.MockTransport(handler))
    assert report["organization"] == "acme"
    assert report["reachable"] is True
    assert report["sampleProjectNames"] == ["Commerce", "Data"]
    assert state["calls"] == 1


def test_repeated_continuation_token_is_a_bounded_failure():
    # A provider that re-offers the same page would otherwise spin forever.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load("projects_p1"))

    client = make_client(handler, sleeps=[])
    with pytest.raises(ConnectorError, match="repeated"):
        list(client.iter_collection("/_apis/projects", {}))


def test_paging_page_budget_is_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("continuationToken", "0")
        next_token = str(int(token) + 1)
        body = {
            "count": 2,
            "value": [{"name": f"p{next_token}"}, {"name": f"q{next_token}"}],
            "continuationToken": next_token,  # fresh token every time: full pages forever
        }
        return httpx.Response(200, json=body)

    client = make_client(handler, sleeps=[])
    with pytest.raises(ConnectorError, match=f"{MAX_COLLECTION_PAGES} pages"):
        list(client.iter_collection("/_apis/projects", {"continuationToken": "0"}))


def test_validate_connection_propagates_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(ConnectorAuthError):
        validate_connection("acme", PAT, transport=httpx.MockTransport(handler))


def test_fetch_projects_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("continuationToken") == "token2":
            return httpx.Response(200, json=load("projects_p2"))
        return httpx.Response(200, json=load("projects_p1"))

    client = make_client(handler, sleeps=[])
    projects = fetch_projects(client)
    assert [p["name"] for p in projects] == ["Commerce", "Data", "Ops"]


def test_pat_never_reaches_log_records(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    client = make_client(handler, [], max_retries=1)
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectorThrottled):
        client.request("/_apis/projects")
    captured = " ".join(
        str(record.getMessage()) + " " + str(record.__dict__) for record in caplog.records
    )
    assert PAT not in captured
    assert base64.b64encode(f":{PAT}".encode()).decode() not in captured


def test_redaction_strips_credentials_from_text():
    header = "Authorization: Basic " + base64.b64encode(f":{PAT}".encode()).decode()
    assert PAT not in redact(header)
    assert PAT not in redact(f"pat: {PAT}")
    assert PAT not in redact(f"token={PAT}")
