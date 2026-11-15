# Security

Each control below is implemented and, where possible, pinned by a test. The
last section lists what is not done. Do not read this document as a promise of
controls the code does not contain.

## Identity and sessions

- **Passwords** are stored as PBKDF2-SHA256 (`enggraph/auth/passwords.py`):
  210,000 iterations, per-user 16-byte random salt, stored form
  `pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>`, compared with
  `hmac.compare_digest`. Raw passwords never reach storage, logs, responses,
  or commits. `scripts/create_user.py` has no `--password` flag (a password in
  argv lands in shell history and the process list) and enforces a minimum
  length (`--min`, default 12).
- **Session tokens** are `<public>.<hmac>`: 32 random urlsafe bytes, signed
  with HMAC-SHA256 keyed by `ENGGRAPH_SESSION_SECRET`. Postgres stores only the
  SHA-256 digest of the full token, so a database leak does not hand out live
  sessions. The signature is verified before any DB lookup, so a forged token
  never reaches the table. Sessions expire after
  `ENGGRAPH_SESSION_TTL_HOURS` (1–72, default 12); logout deletes the row.
- **Login failures are one generic result** (`None`): unknown account,
  inactive user and wrong password are indistinguishable, and the log line
  carries only the local part of the email. `GET /api/v1/auth/me` reports the
  same message for a forged and an expired session, so there is no
  session-state oracle.
- The portal holds the bearer token in `sessionStorage` (per tab), never a
  cookie. A stored token is a claim; the server decides whether it is valid.

## Authorization, server-side

`enggraph/api/deps.py` resolves `Authorization: Bearer <token>` to a
`Principal` (user id, email, role, organization id) and every router takes the
organization a request may read **from the principal**. A client-supplied
organization id is never accepted, so requesting another tenant's entity
returns not-found, not the other tenant's data. `require_admin` guards writes
(connector config, catalog writes, sync job enqueue/retry/cancel). The UI
reflects the role the server declared; it grants nothing.

`enggraph/mcp/server.py` enforces the same rules: each tool resolves the caller
with the same `resolve_principal`, and the organization comes from the
principal, never from an argument. MCP tests assert that a cross-organization
entity is invisible over MCP exactly as it is over REST.

## Secrets

- `ConnectorConfig` has **no PAT column**: `pat_secret_ref` names an
  environment variable. The value is resolved at call time
  (`enggraph/secrets.py`) and is never stored, never returned by an endpoint
  (the connector DTO exposes the reference), never logged.
- `resolve_secret` rejects a reference that is not an environment-variable
  shape and raises a typed configuration error when the value is missing, so
  the failure message names the reference and contains no secret material.
- Neo4j and Postgres credentials come from the environment, `.env` files with
  real values are gitignored, and `.env.example` contains placeholders only.
  `validate_runtime()` reports `CHANGE_ME` placeholders in live mode.
- The MCP stdio token comes from `ENGGRAPH_MCP_TOKEN` in the server process
  environment. There is no token value in the code, and the MCP process warns
  at startup, in advance, that calls will be refused when it is unset.
- The compose file uses `${NEO4J_PASSWORD:?...}` and
  `${POSTGRES_PASSWORD:?...}`, so the stores refuse to start on an empty
  password rather than starting open.

## Log and audit redaction

All log output goes through `logging_setup.setup_logging()`: a
`RedactionFilter` on the root handler and root logger, plus a JSON formatter
that emits the correlation id. The filter rewrites `key=value` / `key: value`
pairs whose key is `authorization`, `pat`, `password`, `token`, `secret`,
`api_key`, `access_token`, and collapses `Authorization: basic|bearer <x>` to
`authorization: [REDACTED]`. Extra fields whose names are in the sensitive set
(`session_secret`, `neo4j_password`, `session_token`, …) are replaced with
`[REDACTED]`. Third-party loggers are covered because the filter is on the root
(`httpx`'s %-style args are folded into the message and cleared).

The same discipline applies to stored rows: job event messages and job `error`
text pass through `redact()`, and audit `details` drop values under a sensitive
key entirely. Tests assert that no credential appears in captured log records
across API, connector, MCP HTTP, and MCP stdio paths, and that MCP tool results
are credential-free.

## Graph query safety

- Every Neo4j query is parameterized (`parameters_`); values never enter the
  query text. Labels and relationship types are validated against
  `NODE_LABELS` / `RELATION_SEMANTICS` (`_safe_label`, `_safe_rel`) and
  rendered as backticked identifiers.
- Every read filters on `organizationId`, is ordered, and carries `LIMIT`
  (plus `SKIP` for pagination). Traversals are bounded per hop by depth
  (`ENGGRAPH_MAX_DEPTH`, default 4) and fanout/limit
  (`ENGGRAPH_MAX_RESULTS`, default 200), and the effective bounds are echoed
  with `truncated` so a reader can tell a bounded answer from a complete one.
- Variable-length path matching (`r:type*1..{depth}`) is built only from an
  integer that FastAPI range-checked (`ge=1, le=10`) and the server then
  clamped to its configured ceiling — never from a string.
- **No user-supplied Cypher exists.** UI and MCP clients reach the graph only
  through reviewed repository methods with fixed relationship allowlists.
  There is no `run_cypher` and no write tool on the MCP surface; every tool is
  annotated read-only, non-destructive, idempotent.

## Network exposure

The portal's nginx and the Vite dev proxy send `/api` to the backend, so a
browser talks to the API and never to the stores. In `docker-compose.yml`,
Neo4j (7687/7474), Postgres (5432), the backend (8000), the MCP endpoint (8100)
and the portal (3000) are published on `127.0.0.1` only. The MCP server binds
loopback by default and rejects a `Host`/`Origin` it does not recognise
(`MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` declare the address clients
present when the server is reached through a published port or a proxy); an
unrelated `Host` gets `421`. Tests cover localhost, a wider bind, and the
container bind.

## Input validation

Request models use `extra="forbid"`. Search query length is capped (120),
catalog names at 200 chars and descriptions at 2000, project name lists at 200
entries, MCP ids at 200 chars, MCP depth 1–10, MCP limit 1–200 — then clamped
to the configured ceilings. Depth/limit/offset arrive as `Query` parameters with
`ge`/`le` bounds. `NodeUpsert`/`EdgeUpsert` validate labels, types, endpoint
semantics, and provenance at construction, so a malformed row is caught before
it reaches the store. Edges are emitted only when both endpoints exist in the
same pass, so no write creates a dangling relationship.

## Audit trail

`AuditRecord` rows (organization, actor, action, target, redacted details) are
written for: `user.create`, `auth.login`, `auth.logout`;
`connector.configure`; catalog writes (`catalog.service_create`,
`catalog.service_update`, `catalog.api_create`, `catalog.relationship_create`,
`catalog.relationship_delete`); `impact.analysis`; `sync.enqueue`,
`sync.retry`, `sync.cancel`, `sync.complete`, `sync.reconcile`; `demo.load` and
`demo.reset` (naming the demo actor). Job event messages, job `error` text and
audit `details` all pass through redaction before they are stored.

## Demo data isolation

Demo fixtures carry `demoData=true`, `sourceSystem="demo"`, a `demoNote`
stating the data is synthetic, and ids namespaced as `demo:{org}:{slug}`. The
loader refuses to load into an organization that already holds non-demo
records; the reset deletes only `demoData=true` nodes in one organization.
Reconciliation never marks demo, manual catalog, or never-observed nodes
deleted. The portal badges demo state, so synthetic data is not presented as
live.

## Not implemented (deliberate gaps, recorded in `docs/known-limitations.md`)

- **No TLS.** Everything is loopback/lan-plain HTTP. Put a TLS-terminating
  proxy in front before serving anything beyond localhost, and prefer short
  session TTLs.
- **No rate limiting and no login lockout.** Repeated failed logins are not
  throttled; there is no request rate limiter.
- **No password change/reset endpoint.** Users are created by the
  `create_user` script; there is no self-service change, and changing a
  password does not revoke existing sessions.
- **No per-project or per-team ACLs.** Roles are `admin` and `viewer`, scoped
  to one organization.
- **No secret manager integration.** Secret references are environment
  variable names; vault/KMS support is not implemented.
- **No CSRF protection work is done because there is no cookie-based
  session.** Tokens are sent as an `Authorization` header from
  `sessionStorage`. This is a property of the design, not a tested control.
- **No automated dependency vulnerability scanning** is configured (no
  lockfile audit step, no `npm audit` / `pip-audit` gate).
- Frontend code has never been typechecked or built in this environment
  (no Node), so its compile-time guarantees are unverified here.
