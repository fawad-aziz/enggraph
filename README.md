# EngGraph

Engineering intelligence platform: a persistent, queryable knowledge
graph across Azure DevOps (work items, repositories, pull requests,
commits, teams, users) plus a manually managed service/API architecture
catalog, exposed via REST API, web portal, and a read-only MCP server.

Design baseline: `project-spec/` (PRD, Technical Architecture, Neo4j
Graph Data Model, Coding Agent Instructions). Implementation status:
`docs/implementation-progress.md`.

## Quick start (Docker Compose — requires Docker)

```bash
cp .env.example .env    # fill in NEO4J_PASSWORD, POSTGRES_PASSWORD, ENGGRAPH_SESSION_SECRET
docker compose up
# API: http://127.0.0.1:8000/api/v1/health
# Portal: http://127.0.0.1:3000
```

Demo mode (`ENGGRAPH_MODE=demo`) runs the full application with clearly
labeled synthetic fixtures, no external credentials. See
`docs/development.md` for local (non-Docker) development and
`docs/azure-devops-setup.md` for live Azure DevOps configuration.

## Layout

- `backend/` — FastAPI app, worker (sync jobs), MCP server, Azure DevOps
  connector, Neo4j graph repository, PostgreSQL persistence.
- `frontend/` — React + TypeScript portal (search, graph explorer,
  catalog, impact analysis).
- `docs/` — architecture, API, MCP, security, operations, limitations.
- `scripts/` — demo data load/reset, user creation.

## Tests

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e backend[dev]   # Windows
PYTHONPATH=backend python -m pytest backend/tests          # unit + contract tests
ENGGRAPH_NEO4J_TEST_URI=bolt://localhost:7687 PYTHONPATH=backend python -m pytest backend/tests -m neo4j
```

Neo4j integration tests run only when `ENGGRAPH_NEO4J_TEST_URI` points
to a reachable Neo4j (e.g. `docker compose up neo4j`); otherwise they
skip with an explicit message — never silently.

## Status

See `docs/implementation-progress.md` for milestone commits, test
results, and known limitations.
