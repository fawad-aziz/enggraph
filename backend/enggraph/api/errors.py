"""API error envelope and exception handlers (architecture doc §8).

Single error shape for every failure:

    {"error": {"code": "...", "message": "...", "correlationId": "..."}}

Messages are safe to return: no credentials, no connection strings, no stack
details. Unexpected failures are logged in full server-side and reported as a
generic 500.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from enggraph.connectors.errors import ConnectorConfigError, ConnectorError
from enggraph.logging_setup import correlation_id, get_logger, redact

log = get_logger("enggraph.api")


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def envelope(code: str, message: str, cid: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "correlationId": cid}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        cid = correlation_id.get()
        log.info(
            "api rejected",
            extra={"code": exc.code, "status": exc.status, "path": request.url.path},
        )
        return JSONResponse(status_code=exc.status, content=envelope(exc.code, exc.message, cid))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        cid = correlation_id.get()
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        message = f"invalid request: {loc} {first.get('type', '')}".strip()
        return JSONResponse(status_code=400, content=envelope("invalid_request", message, cid))

    @app.exception_handler(ConnectorError)
    async def _connector(request: Request, exc: ConnectorError) -> JSONResponse:
        # Connector errors never carry secret material; redact defensively.
        cid = correlation_id.get()
        # A missing secret reference is a server-side configuration problem;
        # everything else is a provider-side failure.
        status = 503 if isinstance(exc, ConnectorConfigError) else 502
        log.warning(
            "connector failure",
            extra={"code": type(exc).__name__, "status": status, "path": request.url.path},
        )
        return JSONResponse(
            status_code=status,
            content=envelope("connector_failure", redact(str(exc)) or "connector failure", cid),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        cid = correlation_id.get()
        log.exception("unhandled API failure", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content=envelope("internal_error", "internal server error", cid),
        )
