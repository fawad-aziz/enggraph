"""Shared Azure DevOps fixture payloads and MockTransport dispatcher.

Fixtures are recorded response shapes (dev.azure.com, api-version 7.2) used by
the client contract tests, the normalization end-to-end test, and the sync
worker tests. They live here so the ingestion path is exercised through the
same recorded payloads everywhere, not hand-built rows.
"""

from __future__ import annotations

import json
import pathlib

import httpx

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ado"
ORG = "acme"
PAT = "pat-abc123def456"
OBSERVED = "2026-11-01T00:00:00Z"
PROJECT = "Commerce"


def load(name: str) -> dict:
    if not name.endswith(".json"):
        name = f"{name}.json"
    return json.loads((FIXTURES / name).read_text())


def handler(request: httpx.Request) -> httpx.Response:
    """Dispatch fixture payloads by ADO endpoint (base_url adds the /acme prefix)."""
    path = request.url.path
    assert path.startswith("/acme"), f"unexpected base path: {path}"
    path = path.removeprefix("/acme")
    if request.method == "POST":
        return httpx.Response(200, json=load("wiql.json"))
    if path == "/_apis/projects":
        # Organization-level project listing (continuation-token paging).
        token = request.url.params.get("continuationToken")
        body = load("projects_p2") if token == "token2" else load("projects_p1")
        return httpx.Response(200, json=body)
    if path.endswith("/workItemReferences"):
        token = request.url.params.get("continuationToken")
        body = load("pr_wi_p2") if token == "w2" else load("pr_wi_p1")
        return httpx.Response(200, json=body)
    if path.endswith("/pullrequests"):
        return httpx.Response(200, json=load("prs.json"))
    if path.endswith("/commits"):
        skip = int(request.url.params.get("searchCriteria[skip]", "0"))
        return httpx.Response(200, json=load("commits_p1" if skip == 0 else "commits_p2"))
    if path.endswith("/members"):
        return httpx.Response(200, json=load("members.json"))
    if path.endswith("/teams"):
        return httpx.Response(200, json=load("teams.json"))
    if path.endswith("/repositories"):
        return httpx.Response(200, json=load("repos.json"))
    if path.endswith("/workitems"):
        return httpx.Response(200, json=load("workitems.json"))
    if path.endswith("/changes"):
        return httpx.Response(200, json=load("changes.json"))
    raise AssertionError(f"unhandled fixture path: {path}")
