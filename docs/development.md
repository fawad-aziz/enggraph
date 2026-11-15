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
.venv/Scripts/python -m enggraph.mcp http   # read-only MCP tools on MCP_PORT (localhost)
.venv/Scripts/python -m enggraph.mcp stdio  # stdio: launched by the MCP client
```

See `docs/mcp-setup.md` for the MCP transports, client configuration, and the
credentials each transport needs.

Load the demo dataset (needs the stores running; `ENGGRAPH_MODE=demo`):

```bash
cd backend && python scripts/demo_load.py --org demo
```

Every record it writes carries `demoData=true`, `sourceSystem="demo"` and a
`demoNote`; the loader refuses an organization that already holds provider or
manual catalog records.

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

- Application DB: migrations are additive — `cd backend && alembic upgrade head`.
  Run it from `backend/`: `script_location = alembic` is resolved relative to the
  current directory. The DSN comes from `POSTGRES_DSN` in `.env`
  (`ENGGRAPH_POSTGRES_DSN` overrides it); the URL in `alembic.ini` is a
  placeholder. `docker compose up` runs it in the backend entrypoint.
  Drop/recreate the database is the destructive alternative.
- Graph schema: `cd backend && python -m enggraph.install_schema` — repeatable,
  every statement is `IF NOT EXISTS`.
- Demo graph data (run from `backend/`):
  `python scripts/demo_reset.py --org demo` (safe reset — deletes only
  `demoData=true` nodes in that organization; provider and catalog records are
  untouched) and `python scripts/demo_load.py --org demo --replace` (safe
  reload). Both exit `2` with a named reason when the configuration is
  incomplete or Neo4j is unreachable, and the loader refuses an organization
  that already holds provider or manual catalog records.
- Backup and full teardown: `docs/troubleshooting.md`.
