"""Search endpoint (FR-05): bounded, paginated, organization-scoped.

The organization is taken from the authenticated principal; a caller cannot ask
for another tenant's entities. `q` is length-bounded at the request layer and
the type filter is validated against the known node labels.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from enggraph.api.deps import (
    MAX_QUERY_LENGTH,
    bounded_limit,
    bounded_offset,
    get_principal,
    get_repo,
)
from enggraph.auth.service import Principal
from enggraph.graph.repository import GraphRepository
from enggraph.services.search import search_entities

router = APIRouter(prefix="/api/v1", tags=["search"])


def split_types(raw: str) -> list[str]:
    """`WorkItem,PullRequest` -> list; empty string means every label."""
    return [p.strip() for p in raw.split(",") if p.strip()]


@router.get("/search")
def search(
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    q: Annotated[str, Query(max_length=MAX_QUERY_LENGTH, description="free text")],
    # Defaults for limit/offset live on the bound validators, not here; `types`
    # is last because it carries the only parameter-level default.
    limit: Annotated[int, Depends(bounded_limit)],
    offset: Annotated[int, Depends(bounded_offset)],
    types: Annotated[str, Query(max_length=MAX_QUERY_LENGTH)] = "",
) -> dict[str, Any]:
    return search_entities(repo, q, principal.organization_id, split_types(types), limit, offset)
