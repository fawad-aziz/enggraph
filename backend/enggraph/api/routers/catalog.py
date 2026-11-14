"""Engineering catalog endpoints (FR-06).

Reads are available to any authenticated member of the organization; writes are
admin-only and go through the catalog service, which stamps manual provenance,
validates endpoints against the relationship semantics, and records an audit
entry for every write. Deleting a relationship is restricted to MANUAL
provenance: provider-derived edges belong to synchronization.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from enggraph.api.deps import (
    bounded_limit,
    bounded_offset,
    get_principal,
    get_repo,
    get_session,
    require_admin,
)
from enggraph.auth.service import Principal
from enggraph.graph.model import RELATION_SEMANTICS
from enggraph.graph.repository import GraphRepository
from enggraph.services.catalog import (
    create_api,
    create_relationship,
    create_service,
    delete_relationship,
    list_label,
    list_manual_relationships,
    update_service,
)

router = APIRouter(prefix="/api/v1", tags=["catalog"])


class ServiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    tier: str = ""

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must be non-empty")
        return value


class ServiceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    tier: str | None = None


class ApiCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    method: str = ""
    path: str = ""
    description: str = ""

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must be non-empty")
        return value


class RelationshipCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relType: str
    srcId: str
    dstId: str

    @field_validator("relType")
    @classmethod
    def _known_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in RELATION_SEMANTICS:
            raise ValueError(f"unknown relationship type: {value}")
        return normalized

    @field_validator("srcId", "dstId")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entity id must be non-empty")
        return value.strip()


@router.get("/services")
def services(
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    limit: Annotated[int, Depends(bounded_limit)],
    offset: Annotated[int, Depends(bounded_offset)],
) -> dict[str, Any]:
    return list_label(repo, principal, "Service", limit, offset)


@router.get("/apis")
def apis(
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    limit: Annotated[int, Depends(bounded_limit)],
    offset: Annotated[int, Depends(bounded_offset)],
) -> dict[str, Any]:
    return list_label(repo, principal, "API", limit, offset)


@router.get("/relationships")
def manual_relationships(
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(get_principal)],
    limit: Annotated[int, Depends(bounded_limit)],
    offset: Annotated[int, Depends(bounded_offset)],
) -> dict[str, Any]:
    return list_manual_relationships(repo, principal, limit, offset)


@router.post("/services", status_code=201)
def create_service_endpoint(
    body: ServiceCreateRequest,
    session: Annotated[Session, Depends(get_session)],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    return create_service(
        session,
        repo,
        principal,
        name=body.name,
        description=body.description,
        tier=body.tier,
    )


@router.patch("/services/{service_id}", status_code=200)
def update_service_endpoint(
    service_id: str,
    body: ServiceUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    return update_service(
        session,
        repo,
        principal,
        service_id_value=service_id,
        name=body.name,
        description=body.description,
        tier=body.tier,
    )


@router.post("/apis", status_code=201)
def create_api_endpoint(
    body: ApiCreateRequest,
    session: Annotated[Session, Depends(get_session)],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    return create_api(
        session,
        repo,
        principal,
        name=body.name,
        method=body.method,
        path=body.path,
        description=body.description,
    )


@router.post("/relationships", status_code=201)
def create_relationship_endpoint(
    body: RelationshipCreateRequest,
    session: Annotated[Session, Depends(get_session)],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    return create_relationship(
        session,
        repo,
        principal,
        rel_type=body.relType,
        src_id=body.srcId,
        dst_id=body.dstId,
    )


@router.delete("/relationships")
def delete_relationship_endpoint(
    # camelCase aliases: the delete request names the same triple as the
    # relationship-create body (relType, srcId, dstId).
    rel_type: Annotated[str, Query(max_length=40, alias="relType")],
    src_id: Annotated[str, Query(max_length=160, alias="srcId")],
    dst_id: Annotated[str, Query(max_length=160, alias="dstId")],
    session: Annotated[Session, Depends(get_session)],
    repo: Annotated[GraphRepository, Depends(get_repo)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    return delete_relationship(
        session,
        repo,
        principal,
        rel_type=rel_type.strip().upper(),
        src_id=src_id,
        dst_id=dst_id,
    )
