"""Canonical DTO shapes for graph reads (API + MCP share these).

DTOs use camelCase, keep the canonical `entityId`, expose the provider source
link where the graph has one, and mark demo data so the UI can label it.
"""

from __future__ import annotations

from typing import Any

from enggraph.graph.model import EdgeView, NodeView

NAME_FIELDS = ("name", "title", "displayName", "hash")


def display_name(node: NodeView) -> str:
    """Human label for a node: name / title / displayName, else the hash for
    commits, else the canonical id (never an empty string)."""
    for key in NAME_FIELDS:
        value = node.props.get(key)
        if value:
            return str(value)
    return node.id


def source_link(node: NodeView) -> str | None:
    url = node.props.get("sourceUrl")
    return str(url) if url else None


def node_dto(node: NodeView) -> dict[str, Any]:
    return {
        "entityId": node.id,
        "entityType": node.label,
        "name": display_name(node),
        "sourceSystem": node.props.get("sourceSystem"),
        "sourceId": node.props.get("sourceId"),
        "sourceUrl": source_link(node),
        "lastSeenAt": node.props.get("lastSeenAt"),
        "isDemoData": bool(node.props.get("demoData")),
        "properties": dict(sorted(node.props.items())),
    }


def edge_dto(edge: EdgeView) -> dict[str, Any]:
    return {
        "relType": edge.rel_type,
        "srcId": edge.src,
        "dstId": edge.dst,
        "provenanceType": edge.props.get("provenanceType"),
        "sourceSystem": edge.props.get("sourceSystem"),
        "sourceRecordId": edge.props.get("sourceRecordId"),
        "observedAt": edge.props.get("observedAt"),
        "createdBy": edge.props.get("createdBy"),
        "properties": dict(sorted(edge.props.items())),
    }


def path_dto(nodes: list[NodeView], edges: list[EdgeView]) -> dict[str, Any]:
    """Path witness: entity list plus each relationship that connects them."""
    return {
        "entities": [
            {"entityId": n.id, "entityType": n.label, "name": display_name(n)} for n in nodes
        ],
        "relationships": [edge_dto(e) for e in edges],
    }
