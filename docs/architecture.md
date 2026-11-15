# Architecture

Describes what is implemented. Nothing here describes a planned feature.

## What runs

| Process | Entry point | Role |
|---|---|---|
| Backend API | `uvicorn enggraph.main:app` (port `BACKEND_PORT`, 8000) | REST API for the portal and any client; health/readiness |
| Sync worker | `python -m enggraph.worker` (`--once`, `--poll-seconds`, `--worker-id`) | Claims sync jobs from Postgres and runs the ingestion pipeline |
| MCP server | `python -m enggraph.mcp stdio` / `python -m enggraph.mcp http` (port `MCP_PORT`, 8100) | Seven read-only tools for MCP clients |
| Portal | `frontend/` (Vite dev server, or nginx in the compose image, port 3000) | React SPA; talks only to `/api` |
| Neo4j | compose service `neo4j` (7687/7474, loopback-published) | The engineering graph |
| PostgreSQL | compose service `postgres` (5432, loopback-published) | Users, sessions, org/connector config, sync jobs, checkpoints, job events, audit, impact reports |

A browser never reaches Neo4j or Postgres. In the compose image the portal's
nginx proxies `/api/` to `backend:8000`; in dev the Vite proxy sends `/api` to
`http://127.0.0.1:8000`. Both stores are published on `127.0.0.1` only, so
they are reachable from the host and not from an arbitrary network peer.

## Layers

```
frontend (React SPA)          MCP client
        │  REST + Bearer            │  MCP tools + Bearer (or ENGGRAPH_MCP_TOKEN)
        ▼                           ▼
enggraph/api  ── routers, request deps, auth principal, bounded depth/limit
        │
        ▼
enggraph/services  ── search, entities, catalog, impact, display
        │                                   enggraph/connectors/azure_devops
        ▼                                   (client, connector, normalize)
enggraph/graph  ── GraphRepository protocol
        │        Neo4jGraphRepository (production)
        │        InMemoryGraphRepository (test-only)
        ▼
   Neo4j

enggraph/persistence  ── SQLAlchemy models + Alembic  →  PostgreSQL
enggraph/jobs         ── queue, job events, audit      →  PostgreSQL
enggraph/mcp          ── MCPServer, stdio + streamable HTTP transports
enggraph/worker       ── worker CLI loop
enggraph/sync         ── pipeline (persist, checkpoints, reconcile), worker job runner
```

Two rules keep this layered:

- Routers are thin. They resolve the principal, validate and clamp arguments,
  and delegate. Business logic lives in `enggraph/services`.
- Domain services depend only on the `GraphRepository` protocol, so the same
  code runs against Neo4j in production and against the in-memory engine in
  tests. Production code never uses the in-memory engine.

The MCP server does not have its own copy of any of this: each tool calls the
same service functions the REST routers call, and resolves the caller with the
same `resolve_principal` the API uses. There is no path that reaches the graph
without passing through the authorization the API applies.

## Request path

1. `main.py` middleware assigns a `x-correlation-id` (from the request header
   if present) and logs the request line; responses carry the id back.
2. `api/deps.py` resolves the bearer token: signature check (HMAC-SHA256 over
   the token's public part) → session lookup by SHA-256 digest → expiry →
   active user. The `Principal` (user id, email, role, organization id) is the
   only source of the organization a request may read. A client-supplied
   organization id is never accepted, so cross-organization reads return "not
   found" rather than another tenant's data.
3. `require_admin` guards write operations.
4. Traversal arguments (`depth`, `limit`, `offset`) are range-checked by FastAPI
   and clamped to `ENGGRAPH_MAX_DEPTH` / `ENGGRAPH_MAX_RESULTS`. The effective
   bounds are echoed in the response, together with `truncated`.

## Ingestion path

- The API enqueues a `SyncJob` (`FULL` or `INCREMENTAL`). The worker claims the
  oldest queued job with a guarded `UPDATE ... WHERE state='QUEUED'` and
  checks rowcount, so exactly one worker wins.
- `sync/worker.py` runs the job: full pass = fetch projects → per project fetch
  the graph snapshot (`fetch_project_graph`) → normalize to canonical rows →
  persist in bounded batches (`BATCH_SIZE = 100`) → advance the checkpoint for
  that project → reconcile after the whole pass. Incremental pass = read the
  per-project checkpoint version → fetch changed work item ids from the Changes
  API → fetch the items → persist → advance the checkpoint to `nextVersion`.
- No DB transaction is held open across a remote API call: fetch first, then
  persist, then advance. `provider_version` advances only after the batch it
  describes has been written, so a failed page leaves the position at the last
  persisted batch and the next run re-fetches it.
- Reconciliation (`mark_not_seen`) runs only on a complete pass. It skips demo
  nodes, manual catalog nodes (`sourceSystem=enggraph`), and nodes with no
  `lastSeenAt`: a provider pass has no evidence about records the provider
  never had.
- Every stage appends a `JobEvent` (seq-ordered, redacted message) and the
  final state is recorded with node/edge/project counts. Failure keeps
  `projects_completed` visible and leaves checkpoints untouched. A retry is a
  new job linked to the failed parent (`attempt` increments), preserving
  history.

The connector is exercised against 13 recorded Azure DevOps payload files
(`tests/fixtures/ado`), served through `tests/ado_fixtures.py`'s `handler` by
`httpx.MockTransport`. The HTTP transport is a
constructor argument, so the exact same client, normalizer, pipeline and API
path run in tests. A live call against a real organization has not been run
(see `docs/known-limitations.md`).

## Secrets

`ConnectorConfig.pat_secret_ref` stores the *name* of an environment variable,
never the PAT. `AZURE_DEVOPS_PAT` (a settings alias) is checked first, so a
`.env`-provided PAT works; any other reference is resolved by
`enggraph/secrets.py` from the environment at call time. The resolved value is
never stored, never returned by an endpoint, and never logged — all log output
passes through `logging_setup.redact()` and a `RedactionFilter`, and audit
payloads drop values under a sensitive key. See `docs/security.md`.

## Modes

`ENGGRAPH_MODE` is `demo` or `live`. `validate_runtime(settings)` names the
missing pieces (empty Neo4j password, missing session secret, live mode without
a PAT/organization, `CHANGE_ME` placeholders), and is used by readiness, by the
MCP process entry point (which exits before opening a transport), and by the
demo scripts (which refuse and exit 2). Demo mode loads a synthetic,
visibly-labeled fixture organization (`demo`); see `docs/development.md`.

## Test seams

`create_app(settings, engine, driver, repo, client_factory, ado_transport)` and
`create_mcp_server(...)` accept injected stores and transports. That is the
mechanism that lets the full request path — auth, authorization, catalog
writes, impact analysis, MCP tools — be exercised without Neo4j, Postgres, or
Azure DevOps credentials. The seam is also the reason the tests can assert
behavior the production code performs, not stubs: the routers, services,
pipeline, and MCP tool bodies under test are the production bodies.
