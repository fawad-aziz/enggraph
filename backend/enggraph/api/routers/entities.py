"""Entity detail, relationships, neighborhood, and path highlighting
(FR-05, FR-08). Every traversal is depth- and limit-bounded, and reports
`truncated` when a bound was reached."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from enggraph.api.deps import (
    bounded_depth,
    bounded_limit,
    get_principal,
    get_repo,
)
from enggraph.api.routers.search import split_types
from enggraph.auth.service import Principal
from enggraph.graph.repository import GraphRepository
from enggraph.services.entities import (
    entity_neighborhood,
    entity_relationships,
    find_paths_between,
    get_entity,
)

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])


@router.get("/{entity_id}")
def entity(
    entity_id: str,
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    return get_entity(repo, entity_id, principal.organization_id)


@router.get("/{entity_id}/relationships")
def relationships(
    entity_id: str,
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    limit: Annotated[int, Depends(bounded_limit)],
) -> dict[str, Any]:
    return entity_relationships(repo, entity_id, principal.organization_id, limit)


@router.get("/{entity_id}/neighborhood")
def neighborhood(
    entity_id: str,
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    depth: Annotated[int, Depends(bounded_depth)],
    limit: Annotated[int, Depends(bounded_limit)],
    node_types: Annotated[str, Query(max_length=120)] = "",
    rel_types: Annotated[str, Query(max_length=120)] = "",
) -> dict[str, Any]:
    return entity_neighborhood(
        repo,
        entity_id,
        principal.organization_id,
        split_types(node_types),
        split_types(rel_types),
        depth,
        limit,
    )


@router.get("/{entity_id}/paths")
def paths(
    entity_id: str,
    to: Annotated[str, Query(max_length=160, description="target entity id")],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    depth: Annotated[int, Depends(bounded_depth)],
    limit: Annotated[int, Depends(bounded_limit)],
    rel_types: Annotated[str, Query(max_length=120)] = "",
) -> dict[str, Any]:
    return find_paths_between(
        repo, entity_id, to, principal.organization_id, split_types(rel_types), depth, limit
    )
