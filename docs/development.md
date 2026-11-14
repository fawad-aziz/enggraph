# Development setup

## Requirements

- Docker + Docker Compose (infrastructure and full app)
- Python 3.12+ (backend, tests)
- Node 20+ (frontend build; not required for backend tests)

## Full stack via Docker Compose

```bash
cp .env.example .env   # set NEO4J_PASSWORD, POSTGRES_PASSWORD, ENGGRAPH_SESSION_SECRET
docker compose up      # frontend, backend, worker, mcp-server, neo4j, postgres
docker compose down    # data persists in named volumes neo4j_data, postgres_data
```

Migrations (`alembic upgrade head`) and graph schema installation run
automatically in the backend entrypoint.

## Local development (no Docker)

1. Start Neo4j and PostgreSQL however you prefer (containers recommended):

```bash
docker compose up neo4j postgres
```

2. Backend:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e backend[dev]   # Windows
# .venv/bin/python -m pip install -e backend[dev]      # POSIX
cp .env.example .env  # fill values; POSTGRES_DSN points at your Postgres
.venv/Scripts/python -m uvicorn enggraph.main:app --reload --port 8000  # from backend/
.venv/Scripts/python -m enggraph.worker
```

3. Frontend:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173, /api proxied
```

## Running checks

```bash
# Unit + connector contract tests (no external services needed):
PYTHONPATH=backend .venv/Scripts/python -m pytest backend/tests
# Neo4j integration tests (needs a reachable Neo4j with your .env credentials):
ENGGRAPH_NEO4J_TEST_URI=bolt://localhost:7687 PYTHONPATH=backend \
  .venv/Scripts/python -m pytest backend/tests -m neo4j
# Lint:
.venv/Scripts/python -m ruff check backend
# Frontend type check/build:
cd frontend && npm run build
```

Current runnability in the original development environment (no Docker,
no Node) and any skipped checks are recorded in
`docs/implementation-progress.md`.

## Reset

- Application DB: `alembic -c backend/alembic.ini upgrade head` (additive
  migrations) or drop/recreate the database.
- Demo graph data: `python scripts/demo_load.py` (clean load) and
  `python scripts/demo_reset.py` (safe reset). See `docs/troubleshooting.md`.
