"""Connector configuration endpoints (FR-02, FR-11).

Secret handling: the API stores a *reference* to the PAT (an environment
variable name). The secret value is resolved only at call time, is never
persisted, never logged, and never returned in a response. Validation runs
against the real Azure DevOps REST API through the same client the sync worker
uses; in tests a mock transport is injected through the app context.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from enggraph.api.deps import AppContext, get_context, get_principal, get_session, require_admin
from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal
from enggraph.connectors.azure_devops.connector import fetch_projects, validate_connection
from enggraph.jobs.queue import audit_record
from enggraph.logging_setup import get_logger
from enggraph.persistence.models import ConnectorConfig, utcnow
from enggraph.secrets import resolve_secret

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])
log = get_logger("enggraph.api.connectors")

MAX_PROJECT_NAMES = 200


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adoOrganization: str
    patSecretRef: str = "AZURE_DEVOPS_PAT"

    @field_validator("patSecretRef")
    @classmethod
    def _env_var_name(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum():
            raise ValueError("patSecretRef must be an environment variable name")
        return value


class ConfigureRequest(ValidateRequest):
    projectNames: list[str]
    pageSize: int = 100

    @field_validator("projectNames")
    @classmethod
    def _bounded(cls, names: list[str]) -> list[str]:
        cleaned = [n.strip() for n in names if n.strip()]
        if not cleaned:
            raise ValueError("at least one project name is required")
        if len(cleaned) > MAX_PROJECT_NAMES:
            raise ValueError(f"too many project names (max {MAX_PROJECT_NAMES})")
        return cleaned


def _dto(row: ConnectorConfig) -> dict[str, Any]:
    """Connector DTO: the secret *reference* is exposed, never the secret."""
    return {
        "id": row.id,
        "organizationId": row.organization_id,
        "provider": row.provider,
        "adoOrganization": row.ado_organization,
        "patSecretRef": row.pat_secret_ref,
        "projectNames": list(row.project_names),
        "pageSize": row.page_size,
        "isActive": row.is_active,
        "lastValidatedAt": row.last_validated_at,
        "lastValidationOk": row.last_validation_ok,
    }


def _resolve_pat(pat_secret_ref: str) -> str:
    """Resolve the PAT from its reference. Missing references fail as typed
    connector configuration errors; the value is never logged."""
    return resolve_secret(pat_secret_ref)


@router.post("/azure-devops/validate")
def validate_connector(
    body: ValidateRequest,
    ctx: Annotated[AppContext, Depends(get_context)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    pat = _resolve_pat(body.patSecretRef)
    result = validate_connection(body.adoOrganization, pat, transport=ctx.ado_transport)
    log.info(
        "connector validated",
        extra={"organization": body.adoOrganization, "secret_ref": body.patSecretRef},
    )
    return {
        "organization": result["organization"],
        "reachable": result["reachable"],
        "sampleProjectNames": result["sampleProjectNames"],
        "secretReference": body.patSecretRef,
    }


@router.post("/azure-devops/configure", status_code=201)
def configure_connector(
    body: ConfigureRequest,
    ctx: Annotated[AppContext, Depends(get_context)],
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    """Store connector configuration after validating it against the provider.

    The PAT is resolved from its reference for the validation call only; the
    stored row keeps the reference, not the value.
    """
    pat = _resolve_pat(body.patSecretRef)
    validation = validate_connection(body.adoOrganization, pat, transport=ctx.ado_transport)
    existing = session.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.organization_id == principal.organization_id,
            ConnectorConfig.ado_organization == body.adoOrganization,
        )
    ).scalar_one_or_none()
    row = existing or ConnectorConfig(
        id=str(uuid.uuid4()),
        organization_id=principal.organization_id,
        provider="azure_devops",
    )
    row.ado_organization = body.adoOrganization
    row.pat_secret_ref = body.patSecretRef
    row.project_names = body.projectNames
    row.page_size = body.pageSize
    row.is_active = True
    row.last_validation_ok = bool(validation["reachable"])
    row.last_validated_at = utcnow()
    session.add(row)
    session.commit()
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="connector.configure",
        target=row.id,
        details={
            "adoOrganization": row.ado_organization,
            "secretReference": row.pat_secret_ref,
            "projectCount": len(row.project_names),
        },
    )
    return _dto(row)


@router.get("/azure-devops/projects")
def list_provider_projects(
    ctx: Annotated[AppContext, Depends(get_context)],
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Live project list from the configured connector (no cached/mock layer)."""
    connector = session.execute(
        select(ConnectorConfig).where(
            ConnectorConfig.organization_id == principal.organization_id,
            ConnectorConfig.is_active == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if connector is None:
        raise ApiError("connector_not_configured", "no active connector for this organization", 404)
    client = ctx.client_factory(connector)
    try:
        projects = fetch_projects(client)
    finally:
        client.close()
    items = [{"name": p.get("name"), "id": p.get("id"), "state": p.get("state")} for p in projects]
    return {"items": items, "total": len(items), "limit": len(items), "offset": 0}
