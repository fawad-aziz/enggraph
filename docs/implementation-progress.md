# Implementation Progress

Commit hash + test/lint results recorded per milestone. Test command:
`cd backend && ../.venv/Scripts/python.exe -m pytest` (run from repo root venv).
Lint: `../.venv/Scripts/python.exe -m ruff check .`.

Neo4j integration tests (`-m neo4j`) are gated on `ENGGRAPH_NEO4J_TEST_URI`;
in this environment (no Docker) they SKIP with an explicit reason — never
silently pass. They are green only when run against a real Neo4j.

| Milestone | Commit | Tests | Lint | Notes |
|---|---|---|---|---|
| 1 — repo scaffold, config, logging, health | `a0169c0` | 11 passed | clean | scaffold + config/logging/health; no Neo4j/ADO code yet |
| 2 — Neo4j schema + graph repositories | `91ebcdd` | 32 passed, 7 skipped (neo4j gate) | clean | graph model, IDs, in-memory engine (test-only), Neo4j repo, schema; Neo4j integration tests env-gated |
| 3 — Azure DevOps connector | `a0c9c47` | 61 passed, 7 skipped (neo4j gate) | clean | real `dev.azure.com` client (PAT Basic auth, api-version 7.2, continuation + skip paging, 429 Retry-After, bounded retries, typed auth/permission errors), normalizer to canonical rows, 13 recorded payload fixtures; live ADO call not run — no credentials in this environment |
| 4 — ingestion, checkpoints, reconciliation | `2b5ba38` | 96 passed, 7 skipped (neo4j gate) | clean | Postgres models + Alembic `0001` (migration applied against SQLite in tests; a live Postgres run was not executed — no Postgres in this environment), guarded job queue (claim/retry/cancel/redacted events), worker CLI (`--once`, `--poll-seconds`), full + incremental sync with checkpoint advancing only after persist, reconciliation only on complete passes, org-mismatch refused before writes, secret references resolved at call time (PAT never stored); live ADO call not run — no credentials in this environment |
| 5 — APIs, search, engineering catalog | _(this commit)_ | 204 passed, 7 skipped (neo4j gate) | clean (ruff check, ruff format: 76 files unchanged, mypy --strict: 51 source files no issues) | FastAPI layer: auth (bearer sessions, admin/viewer), org-scoped reads (cross-org = 404), connectors (validate/configure/projects — secret stored as a reference, validation = 1 live request), sync jobs (enqueue/list/retry/cancel with per-stage events), search (paginated + typed), entity detail/neighborhood/paths (depth+limit bounded, `truncated` reported), manual catalog (allowlisted writes, audited, provider-owned deletes refused), impact analysis (5 categories, path witnesses, `limit+1` truncation detection, limitations section, persisted reports); `docs/api.md` documents the contract. Not live-verified: Postgres (unit tests use SQLite), Neo4j graph repo (in-memory engine implements the same interface), live Azure DevOps (HTTP transport fixtures), frontend (milestone 6 — no Node on this machine) |
