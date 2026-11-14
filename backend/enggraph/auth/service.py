"""User and session domain service (architecture doc §7: authenticated users,
organization-scoped access, admin/viewer roles).

Login failures are reported as one generic result, so the API cannot be used to
enumerate accounts. Sessions are looked up by SHA-256 digest; the raw bearer
token is returned to the caller once at login and never stored.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from enggraph.auth.passwords import hash_password, verify_password
from enggraph.auth.tokens import mint_token, token_hash, verify_token
from enggraph.config import Settings
from enggraph.jobs.queue import audit_record
from enggraph.logging_setup import get_logger
from enggraph.persistence.models import AuthSession, User, UserRoles, utcnow

log = get_logger("enggraph.auth")


def as_utc(dt: datetime.datetime) -> datetime.datetime:
    """Normalize a stored timestamp to aware UTC.

    Postgres returns timezone-aware values; SQLite (used by the schema tests)
    returns naive ones, so comparisons must not depend on the driver.
    """
    return dt.replace(tzinfo=datetime.UTC) if dt.tzinfo is None else dt


@dataclass(frozen=True)
class Principal:
    """Server-side identity of the authenticated caller. Authorization decisions
    are made from this, never from a client-supplied organization id."""

    user_id: str
    email: str
    role: str
    organization_id: str
    session_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    role: str,
    organization_id: str,
    actor: str = "system",
) -> User:
    if role not in UserRoles:
        raise ValueError(f"invalid role: {role}")
    if not organization_id:
        raise ValueError("organization_id is required")
    normalized = email.strip().lower()
    existing = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"email already registered: {normalized}")
    user = User(
        id=str(uuid.uuid4()),
        email=normalized,
        password_hash=hash_password(password),
        role=role,
        organization_id=organization_id,
    )
    session.add(user)
    session.commit()
    audit_record(
        session,
        organization_id=organization_id,
        actor=actor,
        action="user.create",
        target=user.id,
        details={"role": role},
    )
    return user


def login(session: Session, settings: Settings, *, email: str, password: str) -> str | None:
    """Return a signed bearer token, or None for any credential failure."""
    normalized = email.strip().lower()
    user = session.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        # One generic failure path: no distinction between unknown account and
        # wrong password, and nothing credential-shaped is logged.
        log.info("login rejected", extra={"email_local_part": normalized.split("@")[0]})
        return None
    token = mint_token(settings.session_secret)
    row = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=token_hash(token),
        expires_at=utcnow() + datetime.timedelta(hours=settings.session_ttl_hours),
    )
    session.add(row)
    session.commit()
    audit_record(
        session,
        organization_id=user.organization_id,
        actor=user.id,
        action="auth.login",
        target=row.id,
        details={"expires_at": row.expires_at.isoformat()},
    )
    return token


def resolve_principal(session: Session, settings: Settings, token: str) -> Principal | None:
    """Resolve a bearer token to an authorized principal, or None."""
    if verify_token(settings.session_secret, token) is None:
        return None
    row = session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash(token))
    ).scalar_one_or_none()
    if row is None or as_utc(row.expires_at) <= utcnow():
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return Principal(
        user_id=user.id,
        email=user.email,
        role=user.role,
        organization_id=user.organization_id,
        session_id=row.id,
    )


def revoke_session(session: Session, principal: Principal) -> None:
    row = session.get(AuthSession, principal.session_id)
    if row is not None:
        session.delete(row)
        session.commit()
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="auth.logout",
        target=principal.session_id,
    )
