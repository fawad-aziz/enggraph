# Known Limitations

Everything here is a limitation of the **implemented** system, stated as it is.
Nothing in this file describes a feature that exists; nothing here claims a
verified behaviour that was not verified. The "Not verified here" section lists
the checks this environment could not run, with the command that would run them.

## Not verified here (missing infrastructure/credentials)

**Live Azure DevOps.** No PAT and no organization exist in this development
environment, so **no live call against a real Azure DevOps organization has
been made.** The connector is a real `https://dev.azure.com/{org}` REST client
(PAT Basic, `api-version=7.2`, paging, `Retry-After`, typed errors), exercised
against 13 recorded payload files (`backend/tests/fixtures/ado`) served through
`httpx.MockTransport`, which pins request shapes, paging behaviour, throttle
handling, bulk chunking, and every typed error path. The 7-step live sequence
that closes this gap is `docs/azure-devops-setup.md` §6. It needs only
`AZURE_DEVOPS_PAT` + `AZURE_DEVOPS_ORGANIZATION` and a reachable store.

**Real Neo4j.** `backend/tests/test_neo4j_integration.py` runs against a real
Neo4j only when `ENGGRAPH_NEO4J_TEST_URI` points at one; here it skips with an
explicit message. Unverified against a live server: the driver lifecycle
(`open_driver`, pool size 50, 10 s connect timeout), `install_schema` statement
acceptance, index/constraint effects on the queries, Neo4j 5 query semantics
under the `LIMIT`/`SKIP`/depth bounds, and the container's healthcheck
(`cypher-shell`). Run them with:

```bash
docker compose up neo4j
ENGGRAPH_NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_PASSWORD=... \
  PYTHONPATH=backend python -m pytest backend/tests -m neo4j
```

**Real Postgres.** Tests use `sqlite://` in-memory. Never exercised against
PostgreSQL 16: `alembic upgrade head` against a live database, Postgres-specific
column types, the `UPDATE … WHERE state='QUEUED'` claim under real concurrency,
the compose `pg_isready` healthcheck, and the entrypoint's
`alembic upgrade head` → `install_schema` order. The SQLAlchemy models and both
migration files are checked structurally (`tests/test_m4_schema.py` inspects the
schema a migration produces on SQLite), not against Postgres.

**Docker Compose startup.** `docker compose up` has never been run here (no
Docker). `tests/test_compose_contract.py` pins the compose file as a document:
service set, loopback port publishing, `${...:?...}` password guards,
`depends_on` `service_healthy`, volume names, healthchecks, the MCP bind and
its `MCP_ALLOWED_HOSTS`/`MCP_ALLOWED_ORIGINS`, and the backend entrypoint
contents. A running stack, a healthy dependency order, the container network
name (`postgres`, `neo4j`, `backend`), the frontend image build, and the
backup/restore commands in `docs/troubleshooting.md` are all unexecuted.

**Frontend build and browser behaviour.** No Node/npm exists here, so the
portal has never been built (`tsc -b && vite build`), typechecked, rendered, or
clicked through. Its code is strict TypeScript with `strict: true`, and the API
contract it consumes is pinned by backend tests, but compile-time and runtime
behaviour of the UI is unverified. `npm run build` is the missing check.

**Real MCP client applications.** The MCP server is exercised in tests with the
`mcp` SDK's own client (`tests/test_mcp_cli.py`, `tests/test_mcp_http.py`,
`tests/test_mcp_tools.py`): the tool surface, bounded arguments,
authorization, refusals, transports, Host/Origin protection. No configuration
for Claude Desktop, VS Code, Cursor, or any other client has been launched
here, so client-specific config key names and behaviours are unverified.

**Scale, performance, and long runs.** No load test, no benchmark, no
long-running sync against a large organization. The bounds
(`ENGGRAPH_MAX_DEPTH` 4, `ENGGRAPH_MAX_RESULTS` 200, per-collection caps
`MAX_PRS_PER_REPO` 200 / `MAX_COMMITS_PER_REPO` 500 / `MAX_COLLECTION_PAGES`
200, work-item bulk chunk 100) are design limits, not measured throughput.
Response times at realistic data volume, Neo4j query plans, and memory usage are
unmeasured.

## Functional limits of the MVP

- **Search is substring matching, not full-text.** The production search is
  `toLower(coalesce(n.field, '')) CONTAINS $q` over `name`, `title`,
  `displayName`, filtered by label and organization, ordered by label+id,
  paginated with `SKIP`/`LIMIT`, with `total` reported. No tokenization,
  ranking, fuzzy matching, fulltext index, or cross-field scoring. A query for
  `pay` finds `payment-api`; a typo does not.
- **`RELATED_TO` is not produced by the connector.** The type is declared in the
  model, is in the traversal allowlists of the work-item/impact queries, and the
  demo dataset emits one instance, so the traversal path is exercised — the MVP
  Azure DevOps connector does not read ADO's work-item relations, so a live
  organization has no `RELATED_TO` edges. Peer relationships are visible as
  `PARENT_OF` (parent/child) only.
- **`STATIC_ANALYSIS` provenance has no writer.** The type is declared and
  validated, and no component in this build produces it: there is no static
  analysis source and no runtime telemetry ingestion. The writers are the
  connector (`PROVIDER_EXPLICIT`), the manual catalog (`MANUAL`), and the demo
  loader (`PROVIDER_EXPLICIT`, `MANUAL`, `RUNTIME_OBSERVED`). `RUNTIME_OBSERVED`
  is produced only by demo fixtures — no telemetry source is integrated.
- **`INFERRED` findings are one rule.** Cross-consumer inference (two services
  sharing an API) with fixed confidence `0.6`, labelled as a signal for human
  review. It is not a general inference engine, and an "explicit ownership"
  answer never includes an `INFERRED` edge.
- **Traversal is depth-bounded by design.** Reachability beyond
  `ENGGRAPH_MAX_DEPTH` (default 4, ceiling 10) is reported as truncated, not as
  absence. `truncated` is echoed; a bounded answer is not a complete answer.
  There is no path-existence/exhaustive-path enumeration and no full graph
  analytics (centrality, communities, shortest path).
- **No graph write surface for users.** The catalog writes `Service`/`API` and
  the 5 catalog relationship types. Provider relationship types cannot be
  written through the catalog, and a relationship can be deleted only when its
  provenance is `MANUAL` — sync owns provider edges. There is no merge,
  rename, un-delete, or node-delete in the UI, and no way to un-mark
  `isDeleted` other than by the provider seeing the node again.
- **Reconciliation is per-organization, per-connector.** A node marked `isDeleted`
  is excluded from reads; there is no deletion audit trail beyond
  `lastSeenAt`/`isDeleted`, and no report of what changed between passes.
- **Demo dataset is 32 nodes / 54 edges, in one organization.** It covers all 18
  relationship types, 10 labels, all three implemented provenance types, and one
  documented impact scenario (`Orders` exposes `POST /orders` consumed by
  `Checkout`, consumes `POST /tokens` exposed by `Identity` → 2 `INFERRED`
  findings at depth 2). It is small by design. It is not a stress dataset and
  is not a substitute for a real sync.
- **In-memory graph engine is test-only.** `InMemoryGraphRepository` implements
  the `GraphRepository` protocol for tests and is never wired into the runtime
  app. It is not a Neo4j substitute: it does not enforce constraints, indexes,
  query plans, or Neo4j's transaction semantics, so a passing test against it
  does not prove a Neo4j statement is correct.
- **The MCP surface is read-only by construction.** Seven tools, no writes, no
  `run_cypher`, no user-supplied Cypher, no organization argument. A client that
  needs graph writes has no path here, by design.
- **No UI for user management.** Users are created with `scripts/create_user.py`
  (the API's `/auth/users` route is admin-guarded); there is no self-service
  registration, no password change/reset endpoint, and changing a password does
  not revoke existing sessions.
- **Secret references are environment variable names.** No vault/KMS integration;
  rotating a secret means changing the environment variable and re-running.
- **Roles are `admin` and `viewer`, scoped to one organization.** No per-project,
  per-team, per-repository, or per-service ACLs; no read scope narrower than the
  caller's organization.
- **Plain HTTP, loopback-published.** No TLS, no rate limiting, no login lockout.
  Serving beyond localhost requires a TLS-terminating proxy in front, and the
  bearer token in `sessionStorage` is readable by any script that achieves XSS
  in the portal — the design has no cookie (so no CSRF surface) and no
  additional client-side protection.
- **No dependency vulnerability scanning, no CI.** No lockfile audit step, no
  `npm audit`/`pip-audit` gate, no automated pipeline running the checks in
  `docs/development.md`. Frontend dependencies are pinned by `package.json`
  ranges, not a lockfile committed here.
- **API surface is versioned `/api/v1` only.** No pagination contract for
  collection endpoints beyond `limit`/`offset` on search, no idempotency key on
  POSTs, no webhooks, no async export, no rate-limit headers, no OpenAPI
  authentication scheme beyond the bearer token.
- **`APP_VERSION` is `0.1.0`.** An MVP build number, reported by `/health`.
  Not a released product version; there is no release process, changelog, or
  package publication.

## Next steps, in the order they close gaps

1. Run the live Azure DevOps sequence (`docs/azure-devops-setup.md` §6) with a
   read-only PAT, and record the results — the connector's live behaviour is the
   largest unverified area.
2. Run `docker compose up` and the `-m neo4j` tests against the container, then
   `alembic upgrade head` against real Postgres. These close the store gaps
   without any credentials.
3. `npm install && npm run build` in `frontend/`, then a manual pass of the
   pages (login, search, explorer, catalog, impact, sync jobs) against demo data.
4. Configure the MCP server in a real client, list the tools, call one, and
   confirm the read-only annotations appear.
5. Add `RELATED_TO` emission to the connector (ADO work-item relations) so peer
   relationships exist in live organizations.
6. Add a static-analysis or runtime-telemetry producer so `STATIC_ANALYSIS` and
   `RUNTIME_OBSERVED` have real sources.
7. Add a TLS-terminating proxy, a rate limiter, login throttling, and a
   password-change endpoint that revokes sessions before serving anything beyond
   localhost.
8. Add `npm audit` / `pip-audit` and a CI job running
   `ruff check`, `ruff format --check`, `mypy enggraph`, `pytest`, and the
   `-m neo4j` suite against service containers.
