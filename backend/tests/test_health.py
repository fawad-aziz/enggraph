from __future__ import annotations

from fastapi.testclient import TestClient

from enggraph.main import create_app


def test_health_liveness(settings, engine, fake_driver):
    app = create_app(settings=settings, engine=engine, driver=fake_driver)
    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["mode"] == "demo"
        assert resp.headers["x-correlation-id"]


def test_readiness_ok(settings, engine, fake_driver):
    app = create_app(settings=settings, engine=engine, driver=fake_driver)
    with TestClient(app) as client:
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["components"]["postgres"] == "ok"
        assert body["components"]["neo4j"] == "ok"


def test_readiness_degraded_when_neo4j_unreachable(settings, engine):
    from tests.conftest import FakeDriver

    app = create_app(settings=settings, engine=engine, driver=FakeDriver(reachable=False))
    with TestClient(app) as client:
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["components"]["neo4j"] == "unreachable"


def test_readiness_reports_config_problems_without_leaking_values(engine, fake_driver):
    from enggraph.config import Settings

    s = Settings(
        mode="live",
        postgres_dsn="postgresql+psycopg://enggraph:CHANGE_ME@localhost:5432/enggraph",
        neo4j_uri="bolt://test:7687",
        neo4j_user="neo4j",
        neo4j_password="",
        session_secret="",
        azure_devops_pat="",
        azure_devops_organization="",
    )
    app = create_app(settings=s, engine=engine, driver=fake_driver)
    with TestClient(app) as client:
        resp = client.get("/api/v1/health/ready")
        body = resp.json()
        joined = " ".join(body["config_problems"])
        assert "AZURE_DEVOPS_PAT" in joined
        # DSN values and secrets must never be echoed in the response.
        assert "CHANGE_ME@" not in joined
        assert "s3cr3t" not in joined
