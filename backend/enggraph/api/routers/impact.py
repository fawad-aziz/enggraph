"""Change-impact analysis endpoints (FR-09).

Reports are bounded by the configured depth/limit ceilings, persisted
organization-scoped, and readable by id inside the same organization. Every
report includes a limitations section naming the bounds in effect.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from enggraph.api.deps import AppContext, get_context, get_principal, get_repo, get_session
from enggraph.auth.service import Principal
from enggraph.graph.repository import GraphRepository
from enggraph.services.impact import CATEGORY_ORDER, analyze_impact, get_analysis

router = APIRouter(prefix="/api/v1/impact-analysis", tags=["impact"])


class ImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjectId: str
    categories: list[str] = []
    depth: int = 4
    limit: int = 200

    @field_validator("subjectId")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subjectId must be non-empty")
        return value.strip()

    @field_validator("categories")
    @classmethod
    def _known(cls, values: list[str]) -> list[str]:
        normalized = [v.strip().upper() for v in values]
        for v in normalized:
            if v not in CATEGORY_ORDER:
                raise ValueError(f"unknown impact category: {v}")
        return normalized

    @field_validator("depth", "limit")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("depth and limit must be positive")
        return value


@router.post("", status_code=201)
def analyze(
    body: ImpactRequest,
    ctx: Annotated[AppContext, Depends(get_context)],
    session: Annotated[Session, Depends(get_session)],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    return analyze_impact(
        session,
        repo,
        principal,
        subject_id=body.subjectId,
        categories=body.categories,
        depth=body.depth,
        limit=body.limit,
        max_depth=ctx.settings.max_depth,
        max_results=ctx.settings.max_results,
    )


@router.get("/{analysis_id}")
def analysis(
    analysis_id: str,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    return get_analysis(session, principal, analysis_id)
