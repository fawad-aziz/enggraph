"""Request dependencies: app context, DB session, authenticated principal.

Authorization is enforced here (server-side), not in the UI. The organization a
caller may read is taken from the authenticated principal; a client-supplied
organization id is never accepted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated, Protocol, cast

import httpx
from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal, resolve_principal
from enggraph.config import Settings
from enggraph.connectors.azure_devops.client import AdoClient
from enggraph.graph.repository import GraphRepository

MAX_QUERY_LENGTH = 120


class AppContext(Protocol):
    """Structural view of the app context built in `enggraph.main`."""

    settings: Settings
    sessionmaker: sessionmaker[Session]
    repo: GraphRepository | None
    client_factory: Callable[..., AdoClient]
    ado_transport: httpx.BaseTransport | None


def get_context(request: Request) -> AppContext:
    # The context is built in `enggraph.main`'s lifespan; app.state is untyped,
    # so the shape is asserted here, where every request reads it.
    return cast(AppContext, request.app.state.ctx)


def get_session(
    ctx: Annotated[AppContext, Depends(get_context)],
) -> Iterator[Session]:
    session = ctx.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_principal(
    request: Request,
    ctx: Annotated[AppContext, Depends(get_context)],
    session: Annotated[Session, Depends(get_session)],
) -> Principal:
    """Resolve `Authorization: Bearer <token>` to an authorized principal."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError("missing_token", "authentication required", 401)
    token = header[6:].strip()
    if not token:
        raise ApiError("missing_token", "authentication required", 401)
    principal = resolve_principal(session, ctx.settings, token)
    if principal is None:
        # Same message for forged and expired sessions: no session-state oracle.
        raise ApiError("invalid_token", "session is invalid or expired", 401)
    return principal


def require_admin(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if not principal.is_admin:
        raise ApiError("admin_required", "admin role required for this operation", 403)
    return principal


def get_repo(ctx: Annotated[AppContext, Depends(get_context)]) -> GraphRepository:
    if ctx.repo is None:
        raise ApiError("graph_unavailable", "graph store is not connected", 503)
    return ctx.repo


DEFAULT_DEPTH = 4
DEFAULT_LIMIT = 50


# The defaults live on the validator parameters themselves, so endpoints that
# depend on them do not require `depth`/`limit`/`offset` from the caller. Each
# value is still range-checked by Query and clamped to the configured ceiling.
def bounded_depth(
    ctx: Annotated[AppContext, Depends(get_context)],
    depth: Annotated[int, Query(ge=1, le=10)] = DEFAULT_DEPTH,
) -> int:
    """Clamp traversal depth to the configured ceiling (FR-08 bounds)."""
    return min(depth, ctx.settings.max_depth)


def bounded_limit(
    ctx: Annotated[AppContext, Depends(get_context)],
    limit: Annotated[int, Query(ge=1, le=1000)] = DEFAULT_LIMIT,
) -> int:
    return min(limit, ctx.settings.max_results)


def bounded_offset(
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> int:
    return offset
