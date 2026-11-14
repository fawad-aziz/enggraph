# EngGraph — Assumptions and Decisions

Recorded 2026-11-14 during implementation. Non-blocking; each choice is
the simplest secure/maintainable option consistent with the specs.

## Assumptions

1. **Spec docs live in `project-spec/`** (moved from repo root as the
   task instructions described a `project-spec/` directory).
2. **Python 3.14, Node and Docker are unavailable on this machine.**
   Docker Compose files and Dockerfiles are authored to spec and
   documented, but cannot be executed here. Frontend build (`vite`)
   cannot be run here. All Python tests run locally; Neo4j-dependent
   integration tests are env-gated and run in CI/Docker when a Neo4j
   instance is reachable.
3. **No live Azure DevOps credentials.** The real connector is
   implemented against documented REST APIs and tested with HTTP-layer
   fixtures. Live verification steps are documented in
   `docs/azure-devops-setup.md`.

## Decisions

| Area | Decision | Rationale |
|---|---|---|
| Persistence | SQLAlchemy 2.0 (async-friendly) with **Alembic** migrations; Postgres in production, SQLite in unit tests | Spec mandates Postgres; SQLite keeps unit tests runnable without Docker. `psycopg` driver for Postgres. |
| Job queue | **DB-backed queue in PostgreSQL** (worker polls claimed jobs); no Redis | Architecture says prefer simplest reliable implementation; Redis only "if required" — it is not. Persistence/retry documented. |
| Auth | Application users with PBKDF2-hashed passwords, server-side session tokens (bearer), `admin`/`viewer` roles, org-scoped | Spec requires auth + roles + org scope; tokens work identically for REST, MCP, and the UI. Source-system permission mirroring is *not* implemented — documented as limitation. |
| Graph engine | Official `neo4j` Python driver; schema installed at startup; **in-memory graph engine implementing the same repository interface for tests only** | Tests must pass without Docker; production code never touches the in-memory engine. Integration tests run against real Neo4j when `ENGGRAPH_NEO4J_TEST_URI` is set. |
| Demo mode | Explicit `demo` connector + fixture dataset loaded via the *same* graph repository, records marked `demoData=true` + `sourceSystem="demo"`, UI badge, reset script | Spec requires demo mode separated from real connectors. |
| Frontend stack | Vite + React 18 + TypeScript strict, React Router, TanStack Query, Cytoscape.js, hand-rolled CSS (no heavy UI lib) | Matches spec's suggested libraries; keeps bundle small and build simple. |
| MCP transport | FastMCP (`mcp` SDK) with streamable-http for the container deployment; stdio supported for local clients | SDK-supported transports; token via env config; same domain services/authorization as REST. |
| Impact analysis | Allowlist relationship types, bounded depth (≤4), categories: DIRECT, DEPENDENCY, HISTORICAL, OWNERSHIP, INFERRED; paths + provenance + limitations section | Spec §9 of graph model; avoids "nearby ⇒ affected" claim. |
| IDs | Canonical `id` per spec §5 (ado:.../enggraph:...), provider IDs stored separately | Spec-mandated. |
| Provenance | Relationship props `provenanceType`, `sourceSystem`, `sourceRecordId`, `observedAt`, `createdBy`, `createdAt`, `confidence` (inferred only), `validFrom`/`validTo` | Spec §6 architecture + §4 data model. |
| Secrets | PAT from env var / `.env` (gitignored); `.env.example` placeholders; log redaction of `Authorization`/`PAT`/tokens; secrets never returned by APIs | Spec security requirements. |
| Neo4j version | Target Neo4j 5.x (Community); `CREATE CONSTRAINT ... IF NOT EXISTS` syntax (5.x form) | Spec's illustrative Cypher uses 5.x syntax. |
| Incremental sync | ADO Work Item "Updates" API (`Changes` endpoint with `LastModifiedDate`), repo/PR `lastModifiedDate` filters where the API supports them; fallback: lastSeen reconciliation | Only provider-supported markers are used; no fabricated endpoints. |
