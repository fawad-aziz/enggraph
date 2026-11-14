"""`GET /api/v1/organizations` — organization config plus graph counts.

A caller sees exactly one organization: the one their principal belongs to.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from enggraph.api.deps import AppContext, get_context, get_principal, get_session
from enggraph.auth.service import Principal
from enggraph.persistence.models import OrganizationConfig

router = APIRouter(prefix="/api/v1", tags=["organizations"])


@router.get("/organizations")
def list_organizations(
    ctx: Annotated[AppContext, Depends(get_context)],
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    row = session.execute(
        select(OrganizationConfig).where(
            OrganizationConfig.organization_id == principal.organization_id
        )
    ).scalar_one_or_none()
    counts = ctx.repo.counts(principal.organization_id) if ctx.repo is not None else {}
    item = {
        "organizationId": principal.organization_id,
        "displayName": row.display_name if row is not None else principal.organization_id,
        "maxDepth": row.max_depth if row is not None else ctx.settings.max_depth,
        "graphCounts": counts,
        "mode": ctx.settings.mode,
    }
    return {"items": [item], "total": 1, "limit": 1, "offset": 0}
