"""Stable, provider-qualified identifier construction (graph model doc §5).

Format examples:
  ado:{org}:project:{project_id}
  ado:{org}:{project}:repository:{repo_id}
  ado:{org}:{project}:workitem:{id}
  ado:{org}:{project}:pullrequest:{repo_id}:{pr_id}
  ado:{org}:{project}:commit:{repo_id}:{sha}
  enggraph:{org}:service:{uuid}

Provider IDs are preserved separately as `sourceId` on nodes.
"""

from __future__ import annotations


def _safe(component: str | int) -> str:
    """IDs must not embed colons; reject them loudly rather than silently encode.

    Numeric provider values (PR numbers, work item ids) are accepted and
    stringified; graph ids are always strings.
    """
    text = str(component)
    if ":" in text:
        raise ValueError(f"ID component must not contain ':' -> {text!r}")
    if not text:
        raise ValueError("ID component must be non-empty")
    return text


def organization_id(provider: str, org: str) -> str:
    return f"ado:{_safe(provider)}:{_safe(org)}"


def project_id(org: str, project_source_id: str) -> str:
    return f"ado:{_safe(org)}:project:{_safe(project_source_id)}"


def team_id(org: str, team_source_id: str) -> str:
    return f"ado:{_safe(org)}:team:{_safe(team_source_id)}"


def identity_id(org: str, identity_source_id: str) -> str:
    return f"ado:{_safe(org)}:identity:{_safe(identity_source_id)}"


def repository_id(org: str, project: str, repo_source_id: str) -> str:
    return f"ado:{_safe(org)}:{_safe(project)}:repository:{_safe(repo_source_id)}"


def workitem_id(org: str, project: str, wi_source_id: str) -> str:
    return f"ado:{_safe(org)}:{_safe(project)}:workitem:{_safe(wi_source_id)}"


def pullrequest_id(org: str, project: str, repo_source_id: str, pr_source_id: str | int) -> str:
    return (
        f"ado:{_safe(org)}:{_safe(project)}:pullrequest:"
        f"{_safe(repo_source_id)}:{_safe(pr_source_id)}"
    )


def commit_id(org: str, project: str, repo_source_id: str, commit_hash: str) -> str:
    return f"ado:{_safe(org)}:{_safe(project)}:commit:{_safe(repo_source_id)}:{_safe(commit_hash)}"


def service_id(org: str, uuid: str) -> str:
    return f"enggraph:{_safe(org)}:service:{_safe(uuid)}"


def api_id(org: str, uuid: str) -> str:
    return f"enggraph:{_safe(org)}:api:{_safe(uuid)}"
