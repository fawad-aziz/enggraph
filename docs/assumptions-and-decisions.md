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
| MCP transport | `MCPServer` from `mcp` 2.3.0 (the 1.x `FastMCP` is gone from the SDK) with streamable HTTP for the container deployment; stdio for local clients | SDK-supported transports; token via env config; same domain services/authorization as REST. |
| MCP tool surface | Exactly seven read-only tools, all annotated `read_only_hint=True` / `destructive_hint=False` / `idempotent_hint=True`; no `run_cypher`, no write tool, no organization argument | Spec §10 + architecture: MCP must not become a second, less-authorized path into the graph, and must not be able to modify it. |
| MCP stdio credential | `ENGGRAPH_MCP_TOKEN` in the **server process** environment; startup warning when unset; never a fallback for HTTP | stdio has no per-request header, so the operator must supply the identity out of band. Keeping it out of the HTTP path means an unauthenticated request is refused, not silently served as the process identity. |
| MCP client testing | In-process: the SDK's own `ClientSession` + `streamable_http_client` over `ASGITransport`, with `context_for` reproducing the header-borne context the HTTP transport builds | Pins the real wire contract (discovery, `isError`, structured content, Host/Origin validation) without opening a network socket, so MCP tests run without Docker. A real uvicorn bind is exercised by the deployment, not the suite. |
| MCP bind vs client address | `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` settings; container binds all interfaces and declares the published `127.0.0.1:8100` address as allowed | The SDK's DNS-rebinding protection matches the `Host` (port included) against the bound host. A container publishes a loopback port while the process binds every interface, so the bind address is not the address clients present; declaring it keeps protection on and the client reachable. |
| MCP impact analysis | `get_change_impact` persists an analysis record (audit trail) in Postgres; it never writes graph nodes or relationships | "Read-only" is about the graph; the audit trail is the same record the API endpoint writes, so an MCP analysis is visible and reviewable like a UI analysis. Stated in the tool's `caveats`. |
| Impact analysis | Allowlist relationship types, bounded depth (≤4), categories: DIRECT, DEPENDENCY, HISTORICAL, OWNERSHIP, INFERRED; paths + provenance + limitations section | Spec §9 of graph model; avoids "nearby ⇒ affected" claim. |
| IDs | Canonical `id` per spec §5 (ado:.../enggraph:...), provider IDs stored separately | Spec-mandated. |
| Provenance | Relationship props `provenanceType`, `sourceSystem`, `sourceRecordId`, `observedAt`, `createdBy`, `createdAt`, `confidence` (inferred only), `validFrom`/`validTo` | Spec §6 architecture + §4 data model. |
| Secrets | PAT from env var / `.env` (gitignored); `.env.example` placeholders; log redaction of `Authorization`/`PAT`/tokens; secrets never returned by APIs | Spec security requirements. |
| Neo4j version | Target Neo4j 5.x (Community); `CREATE CONSTRAINT ... IF NOT EXISTS` syntax (5.x form) | Spec's illustrative Cypher uses 5.x syntax. |
| Incremental sync | ADO Work Item "Updates" API (`Changes` endpoint with `LastModifiedDate`), repo/PR `lastModifiedDate` filters where the API supports them; fallback: lastSeen reconciliation | Only provider-supported markers are used; no fabricated endpoints. |
| Connector validation | Exactly **one** request (`/_apis/projects`, first page, `VALIDATION_SAMPLE_SIZE = 10`) | Validation answers "do these credentials reach this organization". Paging the whole project list would cost up to `MAX_COLLECTION_PAGES` requests for no extra signal, and would scale with organization size. |
| Truncation observability | Every bounded read reports `truncated`; impact analysis requests `limit + 1` paths per category | A bounded report must be distinguishable from a complete census. `expand_paths` caps its visit budget at `limit`, so a full `limit` paths means the budget was consumed — the extra slot makes the bound visible. |
| Provider provenance | Provider nodes carry `sourceSystem` on the node (not only on edges); manual nodes carry `sourceSystem=enggraph` + `createdBy` | UI and MCP must be able to tell imported from hand-entered records from the node alone, and reconciliation must be able to find provider-scoped nodes. |
| Provider ID fields | Missing provider ID fields **raise** at normalization (`source_value()`), never `str(None)` | A missing id would mint a canonical id that looks real (`ado:acme:None`). Absence is a failure, not a value. |
| Cross-organization reads | `404`, never `403` (`entity_not_found`, `job_not_found`, `analysis_not_found`, `connector_not_found`) | An outside caller must not learn that a resource exists in another organization. The 403 path is reserved for role failures inside the caller's own organization. |
| Session-failure messages | One message for forged/expired/revoked sessions (`session is invalid or expired`); one for login failure (`invalid email or password`) | No oracle for session state or account existence. |
| Manual catalog writes | Allowlist `OWNS, IMPLEMENTED_BY, EXPOSES, CONSUMES, DEPENDS_ON`; delete restricted to `MANUAL` provenance; every write audited | Provider-derived edges belong to synchronization; the manual surface stays small, typed, and reversible. |
| API bounds | `depth` 4 → `ENGGRAPH_MAX_DEPTH`, `limit` 50 → `ENGGRAPH_MAX_RESULTS`, free text 120 chars; clamped, echoed back in the response | Bounds live in one place (`enggraph/api/deps.py`) so every router, the repository, and MCP share them. Clamping (not rejecting) keeps clients from failing on ceiling drift. |
| Format / typing gates | `ruff check` + `ruff format` project-wide; `mypy --strict` over the `enggraph` package only (tests not strict-typed) | Formatting is not a judgement call; typing gates cover shipped code. Test fixtures use unannotated pytest arguments, and `tests/` is deliberately outside mypy's scope. |
| `Settings()` typing | `# type: ignore[call-arg]` on the single construction site | The pydantic mypy plugin types alias-named `BaseSettings` fields as required constructor arguments; every field has a default, verified with a probe. Documented at the call site. |
