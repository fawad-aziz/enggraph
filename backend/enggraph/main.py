"""FastAPI application factory: health/readiness endpoints, app context.

Router registration happens in later milestones. The API layer stays thin:
routers delegate to domain services and graph repositories.
"""

from __future__ import annotations

import contextlib
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from neo4j import Driver
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from enggraph.config import Settings, load_settings, validate_runtime
from enggraph.graph.driver import driver_is_reachable, open_driver
from enggraph.logging_setup import correlation_id, get_logger, setup_logging
from enggraph.persistence.engine import build_engine, build_sessionmaker, db_is_reachable

log = get_logger("enggraph.access")
APP_VERSION = "0.1.0"


class AppContext:
    """Shared application context (settings, DB, graph driver)."""

    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        sessionmaker: sessionmaker[Session],
        driver: Driver | None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.driver = driver


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    driver: Driver | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    setup_logging()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = engine if engine is not None else build_engine(settings.postgres_dsn)
        sess = build_sessionmaker(eng)
        drv = driver if driver is not None else open_driver(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
        )
        app.state.ctx = AppContext(settings, eng, sess, drv)
        yield
        if drv is not None:
            drv.close()

    app = FastAPI(title="EngGraph API", version=APP_VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex[:12]
        correlation_id.set(cid)
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
        response.headers["x-correlation-id"] = cid
        return response

    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok", "version": APP_VERSION, "mode": app.state.ctx.settings.mode}

    @app.get("/api/v1/health/ready")
    def readiness() -> JSONResponse:
        ctx = app.state.ctx
        neo_ok = ctx.driver is not None and driver_is_reachable(ctx.driver)
        components = {
            "backend": "ok",
            "postgres": "ok" if db_is_reachable(ctx.engine) else "unreachable",
            "neo4j": "ok" if neo_ok else "unreachable",
        }
        problems = validate_runtime(ctx.settings)
        ok = all(v == "ok" for v in components.values()) and not problems
        content = {
            "status": "ok" if ok else "degraded",
            "components": components,
            "config_problems": problems,
        }
        return JSONResponse(status_code=200 if ok else 503, content=content)

    return app


def build_runtime_app() -> FastAPI:
    """Entry point for `uvicorn enggraph.main:app` (settings from env)."""
    return create_app()


app = build_runtime_app()
