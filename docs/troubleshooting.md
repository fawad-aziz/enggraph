# Troubleshooting, Backup and Reset

Symptom → cause → fix. Every entry names a message the code actually produces.
MCP-specific symptoms are in `docs/mcp-setup.md` (that section is the
authoritative list for the MCP surface); this file covers the rest.

## Start here

```bash
curl -s http://127.0.0.1:8000/api/v1/health
curl -s http://127.0.0.1:8000/api/v1/health/ready
```

`/health` answers `{"status":"ok","version":"0.1.0","mode":"demo"}` — the
process is up and reports its mode. `/health/ready` answers `200` when
everything is usable, and `503` with:

```json
{"status":"degraded",
 "components":{"backend":"ok","postgres":"ok","neo4j":"unreachable"},
 "config_problems":["NEO4J_PASSWORD is empty"]}
```

`config_problems` is the same list the MCP process refuses on and the demo
scripts exit 2 on, so fix the listed item and re-check. Every request carries
an `x-correlation-id` in the response headers; the same id appears in the
backend's JSON log line for that request, so a portal screenshot's failure can
be matched to a log line.

## Configuration

| Message | Cause | Fix |
|---|---|---|
| `config: ENGGRAPH_SESSION_SECRET is required (session signing)` | session signing key missing | `python -c "import secrets;print(secrets.token_hex(32))"` into `.env` |
| `config: NEO4J_PASSWORD is empty` | empty graph password | set it to the same value the Neo4j container was created with |
| `config: ENGGRAPH_SESSION_SECRET is required in live mode` / `POSTGRES_DSN contains placeholder credentials in live mode` / `AZURE_DEVOPS_PAT is required for live Azure DevOps sync` / `AZURE_DEVOPS_ORGANIZATION is required for live sync` | `ENGGRAPH_MODE=live` without the live inputs | fill them (`docs/azure-devops-setup.md`) or set `ENGGRAPH_MODE=demo` |
| `POSTGRES_PASSWORD is not set` (compose refuses to start a container) | `${POSTGRES_PASSWORD:?...}` guard | set it in `.env`; `.env.example` has the line |
| `FAILED: Path doesn't exist: alembic. Please use the 'init' command to create a new scripts folder` (`backend` exits `255` at the first entrypoint command, so the graph schema never runs and the API never serves) | the image carries `alembic.ini` but not the directory its `script_location = alembic` names, and `alembic upgrade head` runs from the image's `WORKDIR` (`/app`) — the ini is found, the migrations are not | rebuild: `docker compose build backend worker mcp-server` (all three build from `backend/`), then `docker compose up -d`. The Dockerfile now copies `alembic/` to `/app/alembic` next to the ini; `tests/test_compose_contract.py` resolves `script_location` against the build context (`env.py`, `versions/*.py`) and against the `COPY` targets, so the ini and the migrations must both land in the `WORKDIR` |
| `configuration problem: ENGGRAPH_SESSION_SECRET is required (session signing)` from `worker-1`, exit `2` | the worker validates config at startup, and session signing is required in **demo mode as well as live mode** (the portal authenticates in both); the `worker` service's environment did not pass the secret | set it in `.env` (`python -c "import secrets;print(secrets.token_hex(32))"`) — `worker` and `mcp-server` now pass it with the `${ENGGRAPH_SESSION_SECRET:?set ENGGRAPH_SESSION_SECRET in .env}` guard, so compose refuses at parse time instead of starting a container that dies at every restart |
| `Failed to read config: Unrecognized setting. No declared setting with name: X` (the neo4j container exits with code 1, and `backend`, `worker`, `mcp-server`, `frontend` all report `condition "service_healthy" of service "neo4j" not met`) | a `NEO4J_`-prefixed environment variable whose encoded name does not decode to a declared setting | the entrypoint maps every `NEO4J_` variable to a setting: prefix `NEO4J_`, name lowercase, **a period in the setting name is a single `_`, a real underscore is doubled (`__`)**. `dbms.security.auth_enabled` encodes as `NEO4J_dbms_security_auth__enabled`; doubling the wrong place gives a name like `dbms.security_auth.enabled`, which is not a setting, and `NEO4J_SERVER__PROPERTIES` (not a setting at all) fails the same way. The message names the decoded form, so read it and re-encode. One unparseable variable stops the store, and every app service gates on the neo4j healthcheck, so the cascade is expected, not separate failures. `tests/test_compose_contract.py` decodes the names the deployment uses and pins `dbms.security.auth_enabled` |
| `python -m enggraph.mcp` exits `2` before opening a transport, logging `configuration problem: …` | half-configured MCP process | fix `.env`; the refusal is intentional — a misconfigured server never serves a partial surface |

**A password change after the store started does not work.** `NEO4J_AUTH` is
applied when the Neo4j data directory is created; changing `.env` afterwards
leaves the store's real password the original one. If the two disagree, reset
the Neo4j volume (see Reset below) or change the password inside Neo4j.

## Stores

- **`graph_unavailable` (503) from the API, `graph_unavailable` from MCP, and
  readiness showing `"neo4j": "unreachable"`** — the driver could not connect at
  startup, so graph endpoints refuse rather than returning empty results. The
  backend stays up (a `warning` log names the URI). Check `NEO4J_URI`
  (`bolt://localhost:7687` locally, `bolt://neo4j:7687` inside compose), the
  password, and that the store is running. Run `python -m enggraph.install_schema`
  after a fresh Neo4j: it is repeatable (`IF NOT EXISTS` on every statement).
- **Readiness `"postgres": "unreachable"`** — DSN host/credential mismatch, or
  Postgres not running. In compose the DSN is built from `POSTGRES_PASSWORD` and
  the service name `postgres`; in local development `POSTGRES_DSN` must name your
  own Postgres.
- **`alembic` fails with `Can't find file 'alembic.ini'` or a missing
  `alembic` directory** — `script_location = alembic` is resolved relative to the
  current directory. Run it from `backend/`: `cd backend && alembic upgrade head`.
  `alembic/env.py` reads `ENGGRAPH_POSTGRES_DSN` first, then the app's own
  `POSTGRES_DSN`, and falls back to the `CHANGE_ME` URL in `alembic.ini` only
  when neither is set — so `cp .env.example .env` (with a real DSN) is enough.
- **`alembic` connects to the wrong database** — `ENGGRAPH_POSTGRES_DSN` wins
  over `POSTGRES_DSN`. Unset it if it is lingering in your shell.
- **`sqlalchemy` table-missing errors on a fresh database** — migrations have not
  run. `cd backend && alembic upgrade head`; the compose backend entrypoint does
  this automatically at container start.

## API

| Response | Cause | Fix |
|---|---|---|
| `401 {"code":"missing_token"}` | no `Authorization: Bearer` header | log in, send the token (`docs/api.md`) |
| `401 {"code":"invalid_token","message":"session is invalid or expired"}` | expired session (`ENGGRAPH_SESSION_TTL_HOURS`), logged out, or token minted against a different `ENGGRAPH_SESSION_SECRET` | log in again; the token is signed by the server's secret, so a server restart with a new secret invalidates old tokens |
| `401 {"code":"invalid_credentials"}` | unknown email, inactive user, wrong password — one generic message by design | no user enumeration; verify the account with `scripts/create_user.py` |
| `403 {"code":"admin_required"}` | a `viewer` calling a write route (connector config, catalog write, sync enqueue/retry/cancel) | use an admin account; the role comes from the server |
| `404 entity_not_found` for an id you know exists | the entity is in **another organization** (a request may only read the caller's organization), `isDeleted=true` (reconciled away), or the id is not canonical | check the id pattern in `docs/graph-data-model.md`; re-sync, or check the user's organization |
| `404 connector_not_configured` | no active connector row for the organization | `POST /api/v1/connectors/azure-devops/configure` (admin) |
| `404 job_not_found` / `404 analysis_not_found` | row belongs to another organization, or does not exist | organization-scoped reads; there is no cross-tenant lookup |
| `409 job_not_retryable` / `409 job_not_cancellable` | job is not in a retryable/cancellable state | read `GET /api/v1/sync-jobs/{id}` for the state |
| `400 invalid_request` / `400 query is too long` / `400 invalid_entity_type` / `400 invalid_relationship_type` / `400 invalid_category` | argument outside the accepted shape (query ≤ 120 chars, names ≤ 200, descriptions ≤ 2000, known types/categories only, `extra="forbid"`) | shorten/fix the argument; the message names the field |

The portal's UI hides write buttons for a `viewer`, but the server is what
enforces it — a hand-built request gets the same `403` the UI implies.

## Sync jobs

- **A queued job never starts** — no worker process is running. `python -m
  enggraph.worker` (loop, `--poll-seconds` 5 default) or `--once` for one job.
  In compose the `worker` service runs the loop. `--worker-id` names the claimer
  in the job row.
- **Job is `FAILED`** — read `GET /api/v1/sync-jobs/{id}`: the `error` column
  (redacted, capped at 2000 chars) and the event list (`start`,
  `fetch:projects`, `persist:{project}`, `changes:{project}`, `reconcile`,
  `complete`, `failed`) name the stage that failed. `projectsCompleted` is kept
  and **checkpoints are untouched**, so the next run re-fetches the range that
  failed rather than skipping it. Retry is
  `POST /api/v1/sync-jobs/{id}/retry` (admin): a new job, `attempt` incremented,
  parent recorded.
- **Job succeeds, graph looks smaller than expected** — `MAX_PRS_PER_REPO = 200`,
  `MAX_COMMITS_PER_REPO = 500`, `MAX_COLLECTION_PAGES = 200` per collection. A
  large repository is capped by design; the caps are in
  `docs/azure-devops-setup.md`.
- **A node you expected is marked `isDeleted`** — a complete provider pass did not
  see it. Reconciliation never touches demo nodes, manual catalog nodes
  (`sourceSystem=enggraph`), or nodes with no `lastSeenAt`. Reads exclude
  `isDeleted` by default.
- **`job events` show `[REDACTED]`** — intended. Event messages, job `error` text,
  and audit `details` pass through redaction before storage; a `[REDACTED]` value
  means a credential-shaped field was cleared, not that information was lost.

## Connector (live mode)

The connector names its failures; all of these are real messages:

- `authentication failed (401): PAT invalid or expired` — PAT expired/revoked.
  Never retried.
- `access denied (403): PAT lacks required scope for this resource` — scope
  missing (`docs/azure-devops-setup.md` §1 lists the three read scopes).
- `throttled past retry budget for {path}` — `429` budget exhausted (3 retries,
  backoff capped at 30 s, `Retry-After` honoured).
- `transport failure after 3 retries: {type}` — network-level failure.
- `paging exceeded 200 pages for {path}` / `continuation token repeated for {path}`
  — paging guards; raise `page_size` on the connector config, not the cap.
- `project {name} is not in the organization` — configured project name is not in
  the project list the organization returned.
- `secret reference 'X' is not set in the environment` — `pat_secret_ref` names an
  environment variable that is not present.
- `services_base_url` configuration error — organization name is empty or
  contains `: / ? space tab newline`; it is validated so the base URL cannot be
  pointed at another host.

`POST /api/v1/connectors/azure-devops/validate` is exactly one live request
(`GET /_apis/projects?$top=10`), so it answers the credential question cheaply.

## Frontend

- **Portal at `http://127.0.0.1:3000` in compose, `http://127.0.0.1:5173` with
  `npm run dev`** — the compose image serves through nginx (`/api/` →
  `http://backend:8000`), the dev server through the Vite proxy (`/api` →
  `http://127.0.0.1:8000`). A browser talking directly to `:8000` is not the
  portal; the stores are never reachable from the browser.
- **`401` on every portal request** — the token lives in `sessionStorage`
  (per tab). A new tab, a server restart with a new
  `ENGGRAPH_SESSION_SECRET`, or a logout clears/invalidates it: log in again.
- **`npm run build` failures** — the build runs `tsc -b && vite build`, so a type
  error is reported by `tsc` before Vite. This environment has no Node, so the
  frontend has never been built or typechecked here (`docs/known-limitations.md`).

## MCP

The MCP symptom table is in `docs/mcp-setup.md` §Troubleshooting (exit `2`
before transport, `missing_token`/`invalid_token`, stdio's
`ENGGRAPH_MCP_TOKEN` warning, `421` for an unexpected `Host`, `403` for an
unexpected `Origin`, `406` for an `Accept` that omits `text/event-stream`). Two
things worth repeating: the `mcp-server` compose service declares
`MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:*` because clients reach it through a
published port, and a client pointed at a bind address it does not recognise gets
`421` — that is the DNS-rebinding protection working, not a bug.

## Backup

The compose data lives in two named volumes, `neo4j_data` and `postgres_data`.
Back up both.

```bash
# 1. Application rows (users, sessions, org/connector config, jobs,
#    checkpoints, job events, audit, impact reports).
docker compose exec postgres pg_dump -U enggraph enggraph > enggraph-app.sql

# 2. Graph. Neo4j 5 dumps a stopped database, so stop the stack's writers
#    first, then the store, then run neo4j-admin against the volume.
docker compose stop backend worker mcp-server frontend
docker compose stop neo4j
docker run --rm --mount source=<project>_neo4j_data,target=/data neo4j:5 \
  neo4j-admin database dump --database=neo4j --path-to-destination=file:/data/dumps
docker compose up -d neo4j
```

`neo4j.dump` lands in the volume at `dumps/`, outside the database directory.
The volume name is prefixed by the compose project
(`docker volume ls | grep neo4j_data`). Copy the dump somewhere outside the
project before any destructive reset; the app backup is plain text, so a store
password in it is a secret in a file you now own — keep it with `.env`, not in
the repo.

Graph restore (Neo4j stopped, database directory prepared by `neo4j-admin`):

```bash
docker compose stop backend worker mcp-server frontend
docker compose stop neo4j
docker run --rm --mount source=<project>_neo4j_data,target=/data neo4j:5 \
  neo4j-admin database load --from=path:/data/dumps --database=neo4j --overwrite
docker compose up -d neo4j
docker compose up -d backend   # entrypoint re-runs alembic + install_schema
```

App restore into the existing database:

```bash
docker compose exec -T postgres psql -U enggraph -d enggraph < enggraph-app.sql
```

`pg_restore` is available in the container for a custom-format dump. Restoring
into a schema that already has the tables collides with the existing rows; drop
or recreate first (`psql -U enggraph -d postgres -c 'DROP DATABASE enggraph' -c
'CREATE DATABASE enggraph'`, then `alembic upgrade head`).

None of these commands have been executed here — this environment has no Docker.
They are the standard Neo4j 5 / PostgreSQL 16 commands for the images pinned in
`docker-compose.yml`.

## Reset

Choose the narrowest reset that fixes the problem.

```bash
# Demo graph data only (provider + manual catalog records untouched).
cd backend
python scripts/demo_reset.py             # --org demo by default
python scripts/demo_load.py --org demo --replace   # safe reload: demo nodes first

# Graph constraints/indexes only (repeatable).
cd backend && python -m enggraph.install_schema

# Application schema only (DSN comes from POSTGRES_DSN in .env).
cd backend && alembic upgrade head

# Everything: destroy both volumes (destructive — no provider data survives).
docker compose down -v
```

`demo_reset.py` deletes only `demoData=true` nodes in one organization and
leaves the organization's configuration row, so the organization stays visible
with zero counts. `demo_load.py` refuses to load into an organization that
already holds provider or manual catalog records and prints
`refused: …` with exit `2`; the same script prints
`Neo4j is unreachable at bolt://…; start the graph store first` (exit `2`)
rather than doing a partial load. Reconciliation never deletes demo, manual
catalog, or never-observed nodes, so a demo reset is not a side effect of a
sync.

`scripts/create_user.py --email … --role admin --org demo --min 12` prompts for
the password (no `--password` flag) and prints
`refused: email already registered: …` with exit `2` on a duplicate.
