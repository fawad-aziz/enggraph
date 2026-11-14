"""High-level Azure DevOps fetch layer: endpoint paths + normalization wiring.

Fetch functions are thin; they page through ADO REST endpoints and hand raw
payloads to normalize.py. All bounds (page size, per-repo caps, bulk chunk
size) are explicit constants so ingestion runs stay bounded.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from enggraph.connectors.azure_devops.client import AdoClient
from enggraph.connectors.azure_devops.normalize import normalize_project
from enggraph.graph.model import EdgeUpsert, NodeUpsert

BULK_CHUNK_SIZE = 100  # ADO work-item bulk read limit
MAX_PRS_PER_REPO = 200
MAX_COMMITS_PER_REPO = 500
VALIDATION_SAMPLE_SIZE = 10  # first page only: validation is a reachability check


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_projects(client: AdoClient) -> list[dict[str, Any]]:
    return list(client.iter_collection("/_apis/projects", {"$top": client.page_size}))


def fetch_repositories(client: AdoClient, project: str) -> list[dict[str, Any]]:
    path = f"/{project}/_apis/git/repositories"
    return list(client.iter_collection(path, {"$top": client.page_size}))


def fetch_teams(client: AdoClient, project: str) -> list[dict[str, Any]]:
    path = f"/{project}/_apis/projects/{project}/teams"
    return list(client.iter_collection(path, {"$top": client.page_size}))


def fetch_team_members(
    client: AdoClient, project: str, team_source_id: str
) -> list[dict[str, Any]]:
    path = f"/{project}/_apis/projects/{project}/teams/{team_source_id}/members"
    return list(client.iter_collection(path, {"$top": client.page_size}))


def fetch_pull_requests(
    client: AdoClient, project: str, repo_source_id: str
) -> list[dict[str, Any]]:
    path = f"/{project}/_apis/git/repositories/{repo_source_id}/pullrequests"
    items = client.iter_skip_paged(path, {"status": "completed"}, page_size=client.page_size)
    return list(items)[:MAX_PRS_PER_REPO]


def fetch_commits(client: AdoClient, project: str, repo_source_id: str) -> list[dict[str, Any]]:
    path = f"/{project}/_apis/git/repositories/{repo_source_id}/commits"
    items = client.iter_skip_paged(
        path, {"searchCriteria[includeDetails]": "true"}, page_size=client.page_size
    )
    return list(items)[:MAX_COMMITS_PER_REPO]


def fetch_work_item_ids(client: AdoClient, project: str) -> list[int]:
    """Wiql query per project; returns work item ids (bounded by result size)."""
    path = f"/{project}/_apis/wit/wiql"
    body = client.post_json(path, {"query": "SELECT [System.Id] FROM WorkItems"})
    entries = body.get("workItems", []) if isinstance(body, dict) else []
    return [int(e["id"]) for e in entries if e.get("id") is not None]


def fetch_work_items(client: AdoClient, project: str, ids: list[int]) -> list[dict[str, Any]]:
    """Bulk read in chunks of 100 (ADO limit)."""
    path = f"/{project}/_apis/wit/workitems"
    out: list[dict[str, Any]] = []
    for chunk in _chunks(ids, BULK_CHUNK_SIZE):
        body = client.request(path, {"ids": ",".join(str(i) for i in chunk)})
        out.extend(body.get("value", []) if isinstance(body, dict) else [])
    return out


def fetch_work_item_links(
    client: AdoClient, project: str, repo_source_id: str, pr_number: int
) -> list[int]:
    path = (
        f"/{project}/_apis/git/repositories/{repo_source_id}"
        f"/pullRequests/{pr_number}/workItemReferences"
    )
    refs = client.iter_collection(path, {"$top": client.page_size})
    return [int(w["id"]) for w in refs if w.get("id") is not None]


def fetch_changes(client: AdoClient, project: str, version: int) -> dict[str, Any]:
    """Incremental sync source: ADO Changes API (per project).

    Returns {changes: [...], processingRevision, nextVersion}; the checkpoint
    advances to nextVersion only after the page is persisted.
    """
    body = client.request(
        f"/{project}/_apis/wit/changes", {"version": version, "top": client.page_size}
    )
    return body if isinstance(body, dict) else {"changes": []}


def fetch_project_graph(
    client: AdoClient,
    org: str,
    project_raw: dict[str, Any],
    observed_at: str,
) -> tuple[list[NodeUpsert], list[EdgeUpsert]]:
    """Full snapshot for one project: fetch raw, normalize to canonical rows."""
    project_name = project_raw["name"]
    repos = fetch_repositories(client, project_name)
    teams = fetch_teams(client, project_name)
    members: list[tuple[str, dict[str, Any]]] = []
    for team in teams:
        for entry in fetch_team_members(client, project_name, team["id"]):
            members.append((team["id"], entry))

    prs_by_repo: dict[str, list[dict[str, Any]]] = {}
    commits_by_repo: dict[str, list[dict[str, Any]]] = {}
    pr_links: dict[tuple[str, int], list[int]] = {}
    for repo in repos:
        repo_id = repo["id"]
        prs = fetch_pull_requests(client, project_name, repo_id)
        prs_by_repo[repo_id] = prs
        commits_by_repo[repo_id] = fetch_commits(client, project_name, repo_id)
        for pr in prs:
            pr_links[(repo_id, pr["pullRequestId"])] = fetch_work_item_links(
                client, project_name, repo_id, pr["pullRequestId"]
            )

    wi_ids = fetch_work_item_ids(client, project_name)
    work_items = fetch_work_items(client, project_name, wi_ids)

    return normalize_project(
        org,
        project_raw,
        repos,
        teams,
        members,
        prs_by_repo,
        commits_by_repo,
        pr_links,
        work_items,
        observed_at,
    )


def validate_connection(
    organization: str, pat: str, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Validate credentials/organization against the live API (used by the
    /connectors/validate endpoint). Typed errors propagate to the caller.

    Exactly one request is made: validation answers "do these credentials
    reach this organization", and paging the whole project list here would
    cost up to `MAX_COLLECTION_PAGES` requests for no extra signal. The
    sample is the first page only.
    """
    client = AdoClient(organization, pat, transport=transport, max_retries=1)
    try:
        body = client.request("/_apis/projects", {"$top": VALIDATION_SAMPLE_SIZE})
        values = body.get("value") if isinstance(body, dict) else None
        sample = [p.get("name") for p in values if isinstance(p, dict)] if values else []
        return {
            "organization": organization,
            "reachable": True,
            "sampleProjectNames": sample,
        }
    finally:
        client.close()
