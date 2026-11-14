# EngGraph — Implementation Plan

Derived from `project-spec/01..04`. Each milestone ends in one Git commit
(`milestone N: ...`), with its tests passing before the commit.

## Milestone 1 — Foundation & infrastructure

- Repo layout: `backend/` (FastAPI), `frontend/` (React+TS+Vite), worker
  process, MCP server module, `scripts/`, `docs/`.
- `docker-compose.yml`: `frontend`, `backend`, `worker`, `mcp-server`,
  `neo4j`, `postgres` with named volumes, health checks, startup
  dependencies.
- `.env.example` (placeholders only), config loading/validation
  (pydantic-settings), structured logging with secret redaction.
- Health/readiness endpoints (`/api/v1/health`, DB + Neo4j checks).
- Developer setup instructions in `docs/development.md`; test commands.

Acceptance: documented commands start infra; health endpoint works; DB
connectivity verified; no secrets committed.

## Milestone 2 — Neo4j schema & graph repositories

- Schema installation (uniqueness constraints + indexes) run
  automatically at startup, repeatable, versioned (`graph_schema.py`).
- Typed canonical domain models (pydantic) mirroring the data model.
- Graph repository (official neo4j driver, `execute_read`/`execute_write`,
  parameterized bounded templates, idempotent MERGE-based upserts that
  never overwrite with nulls, provenance properties).
- In-memory graph engine implementing the same repository interface for
  tests (test-only, never used by production code).
- Integration tests against a real Neo4j container (env-gated;
  skipped with clear message when no Neo4j is reachable) covering
  constraints, idempotent upserts, deactivation, traversal, org isolation.

Acceptance: schema initializes repeatably; no duplicates after repeated
upserts; directions/properties match spec; org scoping tested.

## Milestone 3 — Azure DevOps connector & normalization

- Provider-neutral connector contract (`EngineeringConnector` protocol:
  validate, list projects/teams, paginated fetch work items/repos/PRs/
  commits, relations).
- Real Azure DevOps Services REST connector (PAT auth, API versions,
  pagination via continuation tokens, 429/Retry-After, transient 5xx
  retries, permission-error mapping, missing optional fields).
- Stable ID construction per spec (`ado:{org}:...`), normalization to
  canonical entities preserving source IDs/URLs/timestamps.
- Contract tests with recorded fixtures: pagination, throttling,
  retries, permission errors, partial data. No live credentials needed
  (responses mocked at HTTP layer); live verification steps documented.

Acceptance: connector not a placeholder; pagination/retry tests pass;
missing credentials → clear config state.

## Milestone 4 — Ingestion, checkpoints, reconciliation

- PostgreSQL models + migrations: users, sessions, org config, connector
  config, sync jobs, checkpoints, job events, audit records.
- Ingestion pipeline: fetch → normalize → resolve references → upsert
  nodes → upsert relationships → checkpoint per page. Checkpoint
  advances only after a page is fully persisted. Bounded batches.
- Initial full sync + incremental sync (ADO WorkItem Updates /
  lastSeen comparison), manual resync, retry of failed jobs.
- Reconciliation marks `lastSeenAt`/`isDeleted` only from complete
  passes; never deletes on partial failures.
- Worker process claims jobs from Postgres (DB-backed queue, documented
  persistence/retry). Job history, counts, errors visible via API.

Acceptance: sync rerunnable safely; partial failures visible; failed page
never skips checkpoint; reconciliation safe.

## Milestone 5 — REST APIs, search, catalog

- `/api/v1` endpoints: auth, organizations, connectors (validate/
  configure/projects), sync-jobs, search, entity detail, relationships,
  neighborhood, services/apis CRUD, manual relationships CRUD (+audit),
  impact analysis.
- Authentication (session tokens, PBKDF2), admin/viewer roles,
  organization-scoped authorization enforced server-side.
- Search on indexed fields with pagination; neighborhood with node/rel
  type filters, bounded expansion.
- Manual catalog writes record actor + `MANUAL` provenance, timestamps.

Acceptance: validated DTOs, unauthorized rejected, catalog auditable,
indexed paginated search.

## Milestone 6 — Web portal, explorer, impact analysis

- React 18 + TypeScript strict, React Router, TanStack Query,
  Cytoscape.js. Pages: login, connectors/sync admin, search, entity
  detail with grouped relationships, neighborhood graph with filters,
  service/API catalog, impact analysis report (direct / indirect /
  historical / inferred, path + provenance + limitations).
- Loading/error/empty/permission states; demo-data badge; source links.

Acceptance: no inert controls; bounded payloads; evidence navigation;
explainable reports.

## Milestone 7 — Read-only MCP server

- FastMCP server reusing the same domain services + authorization
  (bearer token → session). Tools: `search_engineering_entities`,
  `get_entity_context`, `get_repository_context`,
  `get_service_dependencies`, `get_change_impact`,
  `get_related_work_items`, `get_component_owners`.
- Input validation, result/depth limits, structured JSON with
  provenance/timestamps/source URLs/caveats. No `run_cypher`, no writes.
- Contract, authorization, and integration tests (against in-memory
  engine + Neo4j-gated).

Acceptance: tools work on imported data; unauthorized denied; read-only.

## Milestone 8 — E2E, demo data, docs, ops readiness

- Demo fixtures (synthetic org, teams, repos, work items, PRs, commits,
  services, APIs, cross-service deps; impact scenario). Load/reset
  commands. Demo mode visibly labeled.
- E2E tests: fixture ingestion → search → neighborhood → impact → MCP
  tools → provenance checks; org isolation; secret redaction; repeated
  sync idempotency.
- Docs: README, architecture, graph model, ADO setup, security, API,
  MCP setup, development, troubleshooting, known limitations, progress.
- Full-repo review for dead code/placeholders/unsafe defaults.

Acceptance: README-guided fresh run; demo usable; all available checks
pass; live-verification gaps documented; 8 milestone commits.

## Verification strategy in this environment

No Docker, no Node on the dev machine (recorded in
`docs/assumptions-and-decisions.md`). Everything Python is executed with
pytest. Neo4j integration tests are env-gated (`ENGGRAPH_NEO4J_TEST_URI`)
and run via Docker Compose when available. Frontend is written with TS
strict and built via `npm run build` on any machine with Node; its build
is not executable here, so it is explicitly flagged as unverified
locally.
