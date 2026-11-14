"""Entity detail, relationship, and neighborhood reads (FR-05, FR-08).

Neighborhood traversal is depth-bounded and limit-bounded, and reports whether
the bound was reached, so the UI can say "truncated" instead of implying the
graph is complete. Missing entities in another organization are reported as 404
(no cross-tenant enumeration signal).
"""

from __future__ import annotations

from typing import Any

from enggraph.api.errors import ApiError
from enggraph.graph.model import RELATION_SEMANTICS, NodeView
from enggraph.graph.repository import GraphRepository
from enggraph.services.display import edge_dto, node_dto
from enggraph.services.search import validate_types


def _require_node(repo: GraphRepository, node_id: str, org_id: str) -> NodeView:
    node = repo.get_node(node_id, org_id)
    if node is None:
        raise ApiError("entity_not_found", f"entity {node_id} not found", 404)
    return node


def get_entity(repo: GraphRepository, node_id: str, org_id: str) -> dict[str, Any]:
    return node_dto(_require_node(repo, node_id, org_id))


def entity_relationships(
    repo: GraphRepository, node_id: str, org_id: str, limit: int
) -> dict[str, Any]:
    _require_node(repo, node_id, org_id)
    outgoing = repo.outgoing(node_id, org_id, [], limit)
    incoming = repo.incoming(node_id, org_id, [], limit)
    items = [{"direction": "outgoing", **edge_dto(e)} for e in outgoing] + [
        {"direction": "incoming", **edge_dto(e)} for e in incoming
    ]
    return {
        "items": items,
        "total": len(items),
        "limit": limit,
        "subjectId": node_id,
        "truncated": len(items) >= 2 * limit,
    }


def validate_rel_types(rel_types: list[str]) -> list[str]:
    if not rel_types:
        return list(RELATION_SEMANTICS)
    for r in rel_types:
        if r not in RELATION_SEMANTICS:
            raise ApiError("invalid_relationship_type", f"unknown relationship type: {r}", 400)
    return list(rel_types)


def entity_neighborhood(
    repo: GraphRepository,
    node_id: str,
    org_id: str,
    node_types: list[str],
    rel_types: list[str],
    depth: int,
    limit: int,
) -> dict[str, Any]:
    start = _require_node(repo, node_id, org_id)
    labels = validate_types(node_types)
    rels = validate_rel_types(rel_types)
    view = repo.neighborhood(node_id, org_id, labels, rels, depth, limit)
    return {
        "subject": node_dto(start),
        "nodes": [node_dto(n) for n in view.nodes],
        "edges": [edge_dto(e) for e in view.edges],
        "depth": depth,
        "limit": limit,
        "filters": {"nodeTypes": labels, "relTypes": rels},
        "truncated": len(view.nodes) >= limit,
    }


def find_paths_between(
    repo: GraphRepository,
    from_id: str,
    to_id: str,
    org_id: str,
    rel_types: list[str],
    depth: int,
    limit: int,
) -> dict[str, Any]:
    """Path highlighting: every path within the bounds, with its edges."""
    _require_node(repo, from_id, org_id)
    _require_node(repo, to_id, org_id)
    rels = validate_rel_types(rel_types)
    paths = repo.find_paths(from_id, to_id, org_id, rels, depth, limit)
    return {
        "from": from_id,
        "to": to_id,
        "depth": depth,
        "limit": limit,
        "paths": [
            {
                "entities": [node_dto(n) for n in p.nodes],
                "relationships": [edge_dto(e) for e in p.edges],
            }
            for p in paths
        ],
        "truncated": len(paths) >= limit,
    }
