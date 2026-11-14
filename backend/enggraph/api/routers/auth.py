"""Authentication endpoints: login, logout, current identity, user provisioning.

Passwords are verified against PBKDF2 hashes; the bearer token is returned once
at login and stored only as a SHA-256 digest. Login failures use one generic
message, so the endpoint cannot be used to enumerate accounts.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from enggraph.api.deps import AppContext, get_context, get_principal, get_session, require_admin
from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal, create_user, login, resolve_principal, revoke_session
from enggraph.persistence.models import UserRoles

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
MIN_PASSWORD_LENGTH = 8


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str
    role: str = "viewer"


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(
            "invalid_request", f"password must be at least {MIN_PASSWORD_LENGTH} characters", 400
        )


def _principal_dto(principal: Principal) -> dict[str, Any]:
    return {
        "userId": principal.user_id,
        "email": principal.email,
        "role": principal.role,
        "organizationId": principal.organization_id,
        "sessionId": principal.session_id,
    }


@router.post("/login")
def login_endpoint(
    body: LoginRequest,
    ctx: Annotated[AppContext, Depends(get_context)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    _check_password(body.password)
    token = login(session, ctx.settings, email=body.email, password=body.password)
    if token is None:
        raise ApiError("invalid_credentials", "invalid email or password", 401)
    # Re-resolve the fresh session so the response reports the same principal
    # the API will authorize on subsequent requests.
    resolved = login_principal(ctx, session, token)
    return {"token": token, **_principal_dto(resolved)}


def login_principal(ctx: AppContext, session: Session, token: str) -> Principal:
    principal = resolve_principal(session, ctx.settings, token)
    if principal is None:  # pragma: no cover - login just created the session
        raise ApiError("invalid_credentials", "invalid email or password", 401)
    return principal


@router.get("/me")
def me(principal: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
    return _principal_dto(principal)


@router.post("/logout")
def logout(
    principal: Annotated[Principal, Depends(get_principal)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    revoke_session(session, principal)
    return {"revoked": True}


@router.post("/users", status_code=201)
def create_user_endpoint(
    body: CreateUserRequest,
    session: Annotated[Session, Depends(get_session)],
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    """User provisioning is admin-only and scoped to the admin's organization."""
    _check_password(body.password)
    if body.role not in UserRoles:
        raise ApiError("invalid_role", f"role must be one of {', '.join(UserRoles)}", 400)
    user = create_user(
        session,
        email=body.email,
        password=body.password,
        role=body.role,
        organization_id=principal.organization_id,
        actor=principal.user_id,
    )
    return {
        "userId": user.id,
        "email": user.email,
        "role": user.role,
        "organizationId": user.organization_id,
        "isActive": user.is_active,
    }
