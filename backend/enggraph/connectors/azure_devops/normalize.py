"""Normalization of raw Azure DevOps payloads into canonical graph rows.

Rules:
- Canonical IDs are built by graph/ids.py (provider-qualified, colon-safe).
- Provider source IDs are preserved as `sourceId` (+ sourceUrl where ADO gives one).
- Every edge carries provenanceType=PROVIDER_EXPLICIT, sourceSystem,
  sourceRecordId, observedAt.
- Null provider fields are dropped, never written as null over existing values.
- Edges are emitted only when both endpoint nodes were created in the same pass.

Developer identity: ADO identity blocks give a stable `id`; commit authors may
carry only `uniqueName`, which is stable inside one organization, so the
fallback canonical id is `ado:{org}:identity:u.{uniqueName}`.
"""

from __future__ import annotations

from enggraph.graph.ids import (
    commit_id,
    identity_id,
    organization_id,
    project_id,
    pullrequest_id,
    repository_id,
    team_id,
    workitem_id,
)
from enggraph.graph.model import EdgeUpsert, NodeUpsert

SOURCE_SYSTEM = "azure_devops"

# ADO field name -> graph property name (WorkItem)
WORK_ITEM_FIELDS = {
    "System.Title": "title",
    "System.State": "state",
    "System.Reason": "reason",
    "System.WorkItemType": "workItemType",
    "System.Priority": "priority",
    "System.Severity": "severity",
    "System.CreatedDate": "createdDate",
    "System.ChangedDate": "changedDate",
    "System.CreatedBy": "createdByLabel",
    "System.AssignedTo": "assignedToLabel",
}


def provider_org_id(org: str) -> str:
    return organization_id("azure", org)


def developer_from_identity(org: str, identity: dict) -> tuple[str, dict] | None:
    """(canonical id, developer props) from an ADO identity block."""
    if not identity:
        return None
    ident = identity.get("id")
    unique = identity.get("uniqueName")
    if ident:
        node_id = identity_id(org, ident)
    elif unique:
        node_id = identity_id(org, f"u.{unique}")
    else:
        return None
    props = {
        "displayName": identity.get("displayName"),
        "uniqueName": unique,
        "organizationId": org,
    }
    if ident:
        props["sourceId"] = ident
    return node_id, props


def commit_author_identity(commit_raw: dict) -> dict:
    """Commits expose author as {name, uniqueName?, date}; normalize to identity."""
    author = commit_raw.get("author") or {}
    return {
        "displayName": author.get("name") or author.get("displayName"),
        "uniqueName": author.get("uniqueName"),
        "id": None,
    }


def normalize_project(
    org: str,
    project_raw: dict,
    repos: list[dict],
    teams: list[dict],
    members: list[tuple[str, dict]],
    prs_by_repo: dict[str, list[dict]],
    commits_by_repo: dict[str, list[dict]],
    pr_links: dict[tuple[str, int], list[int]],
    work_items: list[dict],
    observed_at: str,
) -> tuple[list[NodeUpsert], list[EdgeUpsert]]:
    """Build canonical rows for one project. Pure function (no network).

    `members` are (team_source_id, member entry {identity:{...}}) pairs;
    `pr_links` maps (repo_source_id, pullRequestId) -> [work item ids]. All
    provider ids are the raw ADO values, scoped to one organization.
    """
    nodes: dict[str, NodeUpsert] = {}
    edges: list[EdgeUpsert] = []

    def add(node: NodeUpsert) -> NodeUpsert:
        existing = nodes.get(node.id)
        if existing:
            existing.props.update(node.sanitized_props())
            return existing
        nodes[node.id] = node
        return node

    def edge(rel: str, src_label: str, src: str, dst_label: str, dst: str,
             source_record_id: str) -> None:
        # Reference resolution: never emit an edge touching a missing node.
        if src not in nodes or dst not in nodes:
            return
        edges.append(
            EdgeUpsert(
                rel_type=rel,
                src_label=src_label,
                src_id=src,
                dst_label=dst_label,
                dst_id=dst,
                props={
                    "provenanceType": "PROVIDER_EXPLICIT",
                    "sourceSystem": SOURCE_SYSTEM,
                    "sourceRecordId": source_record_id,
                    "observedAt": observed_at,
                },
                organization_id=org,
            )
        )

    org_id = provider_org_id(org)
    add(NodeUpsert(
        label="Organization", id=org_id,
        props={"name": org, "provider": SOURCE_SYSTEM, "organizationId": org,
               "lastSeenAt": observed_at},
        organization_id=org,
    ))

    project_source_id = project_raw.get("id")
    project_name = project_raw.get("name")
    pid = project_id(org, project_source_id)
    add(NodeUpsert(
        label="Project", id=pid,
        props={
            "name": project_name,
            "abbreviation": project_raw.get("abbreviation"),
            "description": project_raw.get("description"),
            "state": project_raw.get("state"),
            "sourceId": project_source_id,
            "sourceUrl": f"https://dev.azure.com/{org}/{project_name}",
            "organizationId": org,
            "lastSeenAt": observed_at,
        },
        organization_id=org,
    ))
    edge("HAS_PROJECT", "Organization", org_id, "Project", pid,
         f"project:{project_source_id}")

    for repo in repos:
        repo_source_id = repo.get("id")
        rid = repository_id(org, project_name, repo_source_id)
        add(NodeUpsert(
            label="Repository", id=rid,
            props={
                "name": repo.get("name"),
                "defaultBranch": repo.get("defaultBranchName"),
                "sourceId": repo_source_id,
                "sourceUrl": repo.get("url"),
                "organizationId": org,
                "lastSeenAt": observed_at,
            },
            organization_id=org,
        ))
        edge("CONTAINS_REPOSITORY", "Project", pid, "Repository", rid,
             f"repository:{repo_source_id}")

    for team in teams:
        team_source_id = team.get("id")
        tid = team_id(org, team_source_id)
        add(NodeUpsert(
            label="Team", id=tid,
            props={"name": team.get("name"), "description": team.get("description"),
                   "sourceId": team_source_id, "organizationId": org,
                   "lastSeenAt": observed_at},
            organization_id=org,
        ))
        edge("HAS_TEAM", "Organization", org_id, "Team", tid, f"team:{team_source_id}")

    for team_source_id, entry in members:
        ident = developer_from_identity(org, entry.get("identity") or {})
        if not ident:
            continue
        dev_id, dev_props = ident
        add(NodeUpsert(label="Developer", id=dev_id, props=dev_props, organization_id=org))
        edge("HAS_MEMBER", "Team", team_id(org, team_source_id), "Developer", dev_id,
             f"team-member:{team_source_id}:{(entry.get('identity') or {}).get('id')}")

    # commits (canonical nodes; PR-listed commits dedupe against fetched ones)
    for repo_source_id, raw_commits in commits_by_repo.items():
        repo_id = repository_id(org, project_name, repo_source_id)
        for raw in raw_commits:
            sha = raw.get("commitId")
            cid = commit_id(org, project_name, repo_source_id, sha)
            add(NodeUpsert(
                label="Commit", id=cid,
                props={
                    "hash": sha,
                    "message": raw.get("comments"),
                    "authorDate": (raw.get("author") or {}).get("date"),
                    "sourceId": sha,
                    "organizationId": org,
                    "lastSeenAt": observed_at,
                },
                organization_id=org,
            ))
            edge("CONTAINS_COMMIT", "Repository", repo_id, "Commit", cid,
                 f"commit:{repo_source_id}:{sha}")
            author = developer_from_identity(org, commit_author_identity(raw))
            if author:
                add(NodeUpsert(label="Developer", id=author[0], props=author[1],
                               organization_id=org))
                edge("AUTHORED", "Developer", author[0], "Commit", cid,
                     f"commit-author:{repo_source_id}:{sha}")

    for repo_source_id, prs in prs_by_repo.items():
        repo_id = repository_id(org, project_name, repo_source_id)
        for pr in prs:
            pr_number = pr.get("pullRequestId")
            pr_id = pullrequest_id(org, project_name, repo_source_id, pr_number)
            add(NodeUpsert(
                label="PullRequest", id=pr_id,
                props={
                    "number": pr_number,
                    "title": pr.get("title"),
                    "description": pr.get("description"),
                    "status": pr.get("status"),
                    "createdDate": pr.get("createdDate"),
                    "sourceId": pr_number,
                    "sourceUrl": pr.get("url"),
                    "organizationId": org,
                    "lastSeenAt": observed_at,
                },
                organization_id=org,
            ))
            edge("CONTAINS_PULL_REQUEST", "Repository", repo_id, "PullRequest", pr_id,
                 f"pullrequest:{repo_source_id}:{pr_number}")

            author = developer_from_identity(org, pr.get("createdBy") or {})
            if author:
                add(NodeUpsert(label="Developer", id=author[0], props=author[1],
                               organization_id=org))
                edge("CREATED_BY", "PullRequest", pr_id, "Developer", author[0],
                     f"pr-creator:{repo_source_id}:{pr_number}")

            for reviewer in pr.get("reviewers") or []:
                dev = developer_from_identity(org, reviewer)
                if not dev:
                    continue
                add(NodeUpsert(label="Developer", id=dev[0], props=dev[1],
                               organization_id=org))
                edge("REVIEWED_BY", "PullRequest", pr_id, "Developer", dev[0],
                     f"pr-reviewer:{repo_source_id}:{pr_number}:{reviewer.get('id')}")

            for raw_commit in pr.get("commits") or []:
                sha = raw_commit.get("commitId")
                cid = commit_id(org, project_name, repo_source_id, sha)
                if cid not in nodes:
                    add(NodeUpsert(
                        label="Commit", id=cid,
                        props={"hash": sha, "message": raw_commit.get("comments"),
                               "sourceId": sha, "organizationId": org,
                               "lastSeenAt": observed_at},
                        organization_id=org,
                    ))
                edge("CHANGES", "PullRequest", pr_id, "Commit", cid,
                     f"pr-changes:{repo_source_id}:{pr_number}:{sha}")

    fetched_wi_ids = {str(wi.get("id")) for wi in work_items}
    for wi in work_items:
        wi_source_id = str(wi.get("id"))
        wid = workitem_id(org, project_name, wi_source_id)
        fields = wi.get("fields") or {}
        props = {"organizationId": org, "sourceId": wi_source_id,
                "lastSeenAt": observed_at}
        for ado_field, prop in WORK_ITEM_FIELDS.items():
            props[prop] = fields.get(ado_field)
        props["sourceUrl"] = wi.get("url")
        add(NodeUpsert(label="WorkItem", id=wid, props=props, organization_id=org))

    # parent links only between fetched work items (no dangling references)
    for wi in work_items:
        parent = (wi.get("fields") or {}).get("System.Parent")
        if parent is not None and str(parent) in fetched_wi_ids:
            edge("PARENT_OF", "WorkItem",
                 workitem_id(org, project_name, str(parent)),
                 "WorkItem",
                 workitem_id(org, project_name, str(wi.get("id"))),
                 f"parent:{parent}:{wi.get('id')}")

    for (repo_source_id, pr_number), wi_ids in pr_links.items():
        pr_id = pullrequest_id(org, project_name, repo_source_id, pr_number)
        for wi_id in wi_ids:
            wid = workitem_id(org, project_name, str(wi_id))
            edge("LINKED_TO", "PullRequest", pr_id, "WorkItem", wid,
                 f"pr-workitem:{repo_source_id}:{pr_number}:{wi_id}")

    return list(nodes.values()), edges
