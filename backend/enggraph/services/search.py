"""Search service (FR-05, FR-08): bounded, paginated, entity-typed.

Search runs through the repository interface, whose production implementation
uses the indexed name/organization fields and a parameterized query. Results
are paginated (`limit`/`offset`) and the total count is reported so the UI can
page without unbounded scans.
"""

from __future__ import annotations

from typing import Any

from enggraph.api.errors import ApiError
from enggraph.graph.model import NODE_LABELS
from enggraph.graph.repository import GraphRepository
from enggraph.services.display import node_dto


def validate_types(types: list[str]) -> list[str]:
    """Entity type filter: empty means all labels; anything else must be known."""
    if not types:
        return list(NODE_LABELS)
    for t in types:
        if t not in NODE_LABELS:
            raise ApiError("invalid_entity_type", f"unknown entity type: {t}", 400)
    return list(types)


def search_entities(
    repo: GraphRepository,
    q: str,
    org_id: str,
    types: list[str],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    query = q.strip()
    if len(query) > 120:
        raise ApiError("invalid_request", "query is too long", 400)
    labels = validate_types(types)
    items = repo.search(query, labels, org_id, limit, offset)
    total = repo.search_count(query, labels, org_id)
    return {
        "items": [node_dto(n) for n in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "query": query,
        "types": labels,
    }
