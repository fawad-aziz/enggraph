"""Explainable change-impact analysis (FR-09, milestone 5).

Rules:

- Traversal is bounded by depth and limit, stays inside the caller's
  organization, and uses a documented relationship set per category.
- Every finding carries the path (relationship by relationship) that explains
  why the entity was included, plus the provenance types on that path.
- Findings are grouped by category. Proximity alone is never labelled
  "affected": an entity appears only because a relationship path matching the
  category semantics exists.
- Inferred findings come from one documented rule — services that share an API
  (cross-consumer) — with fixed confidence `INFERRED_CONFIDENCE`. They are
  signals for human review, not verified change impact.
- Every report contains a limitations section naming the bounds in effect.
- Reports are persisted organization-scoped and re-readable by id.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal
from enggraph.graph.model import EdgeView, NodeView
from enggraph.graph.repository import GraphRepository
from enggraph.jobs.queue import audit_record
from enggraph.persistence.models import ImpactAnalysis
from enggraph.services.display import display_name, path_dto, source_link

# Category -> relationship types traversed for that category (spec §9: direct,
# indirect, inferred grouping; FR-09 requires documented sets).
IMPACT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "DIRECT": (
        "HAS_PROJECT",
        "HAS_TEAM",
        "HAS_MEMBER",
        "CONTAINS_REPOSITORY",
        "AUTHORED",
        "CONTAINS_COMMIT",
        "CONTAINS_PULL_REQUEST",
        "CREATED_BY",
        "REVIEWED_BY",
        "LINKED_TO",
        "CHANGES",
        "PARENT_OF",
        "RELATED_TO",
        "OWNS",
        "IMPLEMENTED_BY",
        "EXPOSES",
        "CONSUMES",
        "DEPENDS_ON",
    ),
    "DEPENDENCY": ("DEPENDS_ON", "CONSUMES", "EXPOSES", "IMPLEMENTED_BY"),
    "HISTORICAL": (
        "CONTAINS_COMMIT",
        "CONTAINS_PULL_REQUEST",
        "CHANGES",
        "LINKED_TO",
        "PARENT_OF",
        "RELATED_TO",
        "AUTHORED",
    ),
    "OWNERSHIP": ("HAS_TEAM", "HAS_MEMBER", "OWNS", "CREATED_BY", "REVIEWED_BY"),
}
CATEGORY_ORDER = ("DIRECT", "DEPENDENCY", "HISTORICAL", "OWNERSHIP", "INFERRED")
INFERRED_CONFIDENCE = 0.6


def validate_categories(categories: list[str]) -> list[str]:
    if not categories:
        return list(CATEGORY_ORDER)
    for c in categories:
        if c not in CATEGORY_ORDER:
            raise ApiError("invalid_category", f"unknown impact category: {c}", 400)
    return list(categories)


def _finding(
    category: str,
    nodes: list[NodeView],
    edges: list[EdgeView],
    confidence: float,
) -> dict[str, Any]:
    target = nodes[-1]
    provenance = sorted({str(e.props.get("provenanceType")) for e in edges})
    return {
        "category": category,
        "entityId": target.id,
        "entityType": target.label,
        "name": display_name(target),
        "sourceUrl": source_link(target),
        "depth": len(edges),
        "confidence": confidence,
        "provenanceTypes": provenance,
        "hasManualEvidence": any(e.props.get("provenanceType") == "MANUAL" for e in edges),
        "lastSeenAt": target.props.get("lastSeenAt"),
        "path": path_dto(nodes, edges),
    }


def _nodes_along(
    start: NodeView, edges: list[EdgeView], repo: GraphRepository, org: str
) -> list[NodeView] | None:
    """Node list following the edge order; stops if an endpoint is unreadable."""
    nodes = [start]
    for edge in edges:
        other_id = edge.dst if edge.src == nodes[-1].id else edge.src
        node = repo.get_node(other_id, org)
        if node is None:
            return None
        nodes.append(node)
    return nodes


def _cross_consumer_findings(
    subject: NodeView, repo: GraphRepository, org: str, limit: int
) -> list[tuple[str, str, list[EdgeView]]]:
    """Documented inference rule: services sharing one API.

    - Service subject exposing an API -> services consuming that API.
    - Service subject consuming an API -> services exposing that API.
    - API subject -> consumers of the API exposed by its provider service.

    Each result is `(path_start, finding, edges)`. The edges form a connected
    chain from `path_start` to `finding`, the analyzed subject is on that chain,
    and `edges[0]` always touches the subject. For a service subject the chain
    starts at the subject; for an API subject both edges point at the subject,
    so the chain starts at the exposing provider.
    """
    triples: list[tuple[str, str, list[EdgeView]]] = []
    seen: set[tuple[str, str]] = set()

    def add(start_id: str, target_id: str, edges: list[EdgeView]) -> None:
        # Dedupe is (peer, shared API); `edges[0].dst` is the shared API in all
        # three arms.
        key = (target_id, edges[0].dst)
        if target_id == subject.id:
            return
        if key in seen:
            return
        seen.add(key)
        triples.append((start_id, target_id, edges))

    if subject.label == "Service":
        for expose in repo.outgoing(subject.id, org, ["EXPOSES"], limit):
            for consume in repo.incoming(expose.dst, org, ["CONSUMES"], limit):
                add(subject.id, consume.src, [expose, consume])
        for consume in repo.outgoing(subject.id, org, ["CONSUMES"], limit):
            for expose in repo.incoming(consume.dst, org, ["EXPOSES"], limit):
                # The consuming edge leaves the subject, so it comes first: the
                # witness runs subject -> API -> the service that exposes it.
                add(subject.id, expose.src, [consume, expose])
    elif subject.label == "API":
        for expose in repo.incoming(subject.id, org, ["EXPOSES"], limit):
            for consume in repo.incoming(subject.id, org, ["CONSUMES"], limit):
                # Provider -> API -> consumer: the shared API sits in the middle.
                add(expose.src, consume.src, [expose, consume])
    return triples


def limitations_text(depth: int, limit: int, categories: list[str], truncated: bool) -> list[str]:
    """Limitations section required in every report (FR-09)."""
    lines = [
        f"Traversal is bounded to depth {depth} and {limit} entities per category; "
        "the report is not a complete impact census.",
        "Findings are relationship-path results. Proximity alone is never treated as "
        "impact: an entity is listed only when a relationship path matching the "
        "category semantics exists.",
        "Graph data reflects the last successful synchronization (node lastSeenAt); "
        "changes made in Azure DevOps after that are not visible.",
        "Services, APIs, and manual relationships exist only where they have been "
        "configured in the catalog. Absent entries mean unconfigured, not absent.",
        "Source-system access control is not mirrored: everyone authorized in EngGraph "
        "sees the same graph for their organization.",
    ]
    if "INFERRED" in categories:
        lines.append(
            f"Inferred findings come from one documented rule (services sharing an API) "
            f"with fixed confidence {INFERRED_CONFIDENCE}. They are signals for human "
            "review, not verified change impact."
        )
    if truncated:
        lines.append("At least one traversal hit its bound: the result set is truncated.")
    return lines


def analyze_impact(
    session: Session,
    repo: GraphRepository,
    principal: Principal,
    *,
    subject_id: str,
    categories: list[str],
    depth: int,
    limit: int,
    max_depth: int,
    max_results: int,
) -> dict[str, Any]:
    subject = repo.get_node(subject_id, principal.organization_id)
    if subject is None:
        raise ApiError("entity_not_found", f"entity {subject_id} not found", 404)
    categories = validate_categories(categories)
    depth = min(depth, max_depth)
    limit = min(limit, max_results)
    org = principal.organization_id
    analysis_id = str(uuid.uuid4())

    findings: dict[tuple[str, str], dict[str, Any]] = {}
    truncated = False
    for category in categories:
        if category == "INFERRED":
            continue
        # One slot beyond the limit is fetched so a bound hit is observable: the
        # repository returns at most `limit + 1` paths, and a full extra slot
        # means the traversal ran out of budget. Without this, a bounded report
        # is indistinguishable from a complete census.
        paths = repo.expand_paths(
            subject_id, org, list(IMPACT_CATEGORIES[category]), depth, limit + 1
        )
        # `expand_paths` caps visited nodes at its `limit`, so a full `limit`
        # paths means the traversal consumed the whole budget.
        if len(paths) >= limit:
            truncated = True
            paths = paths[:limit]
        for path in paths:
            if len(path.nodes) < 2:
                continue
            key = (category, path.nodes[-1].id)
            if key in findings:
                continue
            findings[key] = _finding(category, path.nodes, path.edges, 1.0)

    if "INFERRED" in categories:
        triples = _cross_consumer_findings(subject, repo, org, limit + 1)
        if len(triples) > limit:
            truncated = True
        for start_id, target_id, edges in triples[:limit]:
            start = repo.get_node(start_id, org)
            if start is None:
                continue
            nodes = _nodes_along(start, edges, repo, org)
            if nodes is None:
                continue
            key = ("INFERRED", target_id)
            if key in findings:
                continue
            findings[key] = _finding("INFERRED", nodes, edges, INFERRED_CONFIDENCE)

    ordered = sorted(
        findings.values(),
        key=lambda f: (CATEGORY_ORDER.index(f["category"]), f["depth"], f["entityId"]),
    )
    counts = {c: sum(1 for f in ordered if f["category"] == c) for c in CATEGORY_ORDER}
    created_at = datetime.datetime.now(datetime.UTC)
    report = {
        "analysisId": analysis_id,
        "organizationId": org,
        "subjectId": subject.id,
        "subjectType": subject.label,
        "subjectName": display_name(subject),
        "sourceUrl": source_link(subject),
        "requestedBy": principal.user_id,
        "categories": categories,
        "depth": depth,
        "limit": limit,
        "createdAt": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
        "findings": ordered,
        "truncated": truncated,
        "limitations": limitations_text(depth, limit, categories, truncated),
    }
    session.add(
        ImpactAnalysis(
            id=analysis_id,
            organization_id=org,
            requested_by=principal.user_id,
            subject_id=subject.id,
            categories=categories,
            depth=depth,
            limit=limit,
            result_count=len(ordered),
            report=report,
        )
    )
    session.commit()
    audit_record(
        session,
        organization_id=org,
        actor=principal.user_id,
        action="impact.analysis",
        target=analysis_id,
        details={"subjectId": subject.id, "depth": depth, "results": len(ordered)},
    )
    return report


def get_analysis(session: Session, principal: Principal, analysis_id: str) -> dict[str, Any]:
    """Reports are readable only inside the requesting organization."""
    row = session.execute(
        select(ImpactAnalysis).where(
            ImpactAnalysis.id == analysis_id,
            ImpactAnalysis.organization_id == principal.organization_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError("analysis_not_found", f"analysis {analysis_id} not found", 404)
    return row.report
