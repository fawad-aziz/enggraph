# EngGraph — Coding Agent Execution Instructions

**Read this document together with the PRD, Technical Architecture, and
Neo4j Graph Data Model before changing code.**

## 1. Mission

Implement the complete EngGraph MVP described in the accompanying
documents.

**Important execution requirement: complete the entire MVP in one
continuous autonomous development effort. Do not stop after each
milestone and wait for the user.**

You must still organize implementation into the milestones below and
create a separate Git commit after each milestone is complete and
verified.

The milestones are commit boundaries and progress checkpoints, not user
approval gates.

Continue from one milestone to the next automatically unless blocked by
a genuine external dependency that cannot be worked around, such as
unavailable credentials or an unavailable required service. Even then,
implement and test the remaining functionality using clearly separated
fixtures/demo mode wherever possible.

## 2. Read and understand the specifications

Before coding: 1. Read all four project documents. 2. Inspect the
existing repository, branch, files, conventions, and Git status. 3.
Identify existing code that should be preserved. 4. Produce a concise
implementation plan in `docs/implementation-plan.md`. 5. Record
assumptions and unresolved non-blocking decisions in
`docs/assumptions-and-decisions.md`. 6. Do not overwrite or delete
existing user work. 7. Do not create a new project inside another
project if a repository structure already exists.

If this is an empty repository, initialize the project using the
architecture in the documents.

## 3. Autonomy and clarification policy

Do not ask the user to approve each milestone.

Use the documented defaults: - Azure DevOps Services as the first
integration. - Python + FastAPI backend. - React + TypeScript
frontend. - Neo4j Community. - PostgreSQL. - Docker Compose. - Read-only
MCP server. - PAT supplied through environment/secret configuration for
initial development. - One configured organization, with
organization-scoped data model and authorization.

If a decision is not specified: 1. Choose the simplest secure and
maintainable option consistent with the documents. 2. Record the choice
and rationale in `docs/assumptions-and-decisions.md`. 3. Continue.

Ask the user only when a genuine decision cannot safely be made or an
external authorization/credential is essential. Do not stop all
development because a credential is missing.

## 4. No fake completion

The following are prohibited: - Hardcoded fake data presented as live
Azure DevOps data. - Mock connector methods presented as implemented
integrations. - UI buttons that do nothing. - Placeholder API endpoints
presented as functional. - Catch-all exception handling that hides
failures. - Tests that only assert that code imports. - Unbounded Cypher
queries. - Unrestricted Cypher execution from the UI or MCP. -
Credentials in source code, tests, logs, screenshots, or commits. -
Claiming an integration works without an actual integration test or a
clearly documented reason it could not be exercised.

Demo fixtures are allowed only in an explicit demo mode, visibly
identified as demo data. Keep demo mode separate from real connector
behavior.

If credentials are unavailable: - Implement the real connector using
documented APIs. - Add mocked contract tests using representative
fixtures. - Add configuration validation and clear setup instructions. -
Keep the app runnable in demo mode. - Document the exact live
verification steps still requiring credentials. - Continue implementing
all other features.

## 5. Git and commit policy

Use the current branch unless repository policy or existing instructions
require a feature branch. Do not push to a remote unless the user
explicitly asks.

Before changes: - Inspect `git status`. - Do not discard uncommitted
user changes. - If the working tree contains unrelated changes, preserve
them and avoid staging them.

Create one meaningful commit at the end of each milestone:

1.  `milestone 1: establish project foundation and infrastructure`
2.  `milestone 2: implement Neo4j schema and graph repositories`
3.  `milestone 3: implement Azure DevOps connector and normalization`
4.  `milestone 4: implement ingestion, checkpoints, and reconciliation`
5.  `milestone 5: implement APIs, search, and engineering catalog`
6.  `milestone 6: implement graph explorer and impact analysis`
7.  `milestone 7: implement secured read-only MCP tools`
8.  `milestone 8: complete end-to-end tests, documentation, and operational readiness`

Adapt the exact commit messages if the existing repository has a
convention, but retain one commit per completed milestone.

For every milestone: - Run its relevant tests. - Run formatting,
linting, and type checks where configured. - Fix failures before
committing. - Review the staged diff. - Ensure no secrets or generated
junk are staged. - Commit only the work belonging to that milestone. -
Record commit hash and test results in
`docs/implementation-progress.md`.

Do not make an empty milestone commit. If a milestone is genuinely not
applicable, document why and continue.

If a test cannot run because of missing credentials or unavailable
external infrastructure, distinguish that from a passing test and
provide a substitute contract/integration test where possible.

## 6. Required implementation milestones

### Milestone 1 — Project foundation and infrastructure

- Establish backend, frontend, worker, and MCP project structure.
- Add Docker Compose for Neo4j and PostgreSQL and application services.
- Add health checks and persistent database volumes.
- Add `.env.example` with placeholders.
- Add secure configuration loading and validation.
- Add logging with secret redaction.
- Add developer setup instructions.
- Add initial CI/test commands if repository supports CI.

Acceptance: - A developer can start the infrastructure using documented
commands. - Backend health endpoint works. - Database connectivity is
verified. - No secrets are committed.

### Milestone 2 — Neo4j schema and graph repositories

- Implement the graph model from the data-model document.
- Add schema initialization/migration mechanism.
- Add uniqueness constraints and appropriate indexes.
- Implement typed domain models and graph repository functions.
- Implement parameterized, bounded query templates.
- Implement idempotent node and relationship upserts.
- Add graph integration tests using a real Neo4j test
  instance/container.

Acceptance: - Schema initializes repeatably. - Repeated upserts create
no duplicates. - Relationship direction and properties match the
specification. - Tests verify organization scoping and graph query
behavior.

### Milestone 3 — Azure DevOps connector and normalization

- Implement an actual Azure DevOps Services connector using supported
  APIs.
- Support secure PAT configuration.
- Validate organization/project access.
- Enumerate selected projects.
- Import supported teams, identities, repositories, work items, pull
  requests, commits, and explicit relations where APIs permit.
- Handle pagination, throttling, transient failures, missing optional
  fields, and permission errors.
- Normalize provider records into canonical models.
- Preserve provider IDs, timestamps, URLs, and relation types.
- Add connector contract tests with fixtures.

Acceptance: - Connector is not a placeholder. - Provider API behavior is
documented. - Pagination and retry tests pass. - Missing credentials
produce a clear configuration state, not fabricated success.

### Milestone 4 — Ingestion, checkpoints, and reconciliation

- Implement initial full synchronization.
- Implement incremental synchronization using provider-supported update
  markers.
- Persist job status, counts, errors, and checkpoints in PostgreSQL.
- Make each stage retryable and idempotent.
- Ensure checkpoint advancement cannot skip failed pages.
- Implement stale/deleted/archived reconciliation only where source
  semantics justify it.
- Add manual sync and retry actions.
- Add sync job history and progress reporting.

Acceptance: - A sync can be rerun safely. - Partial failures are
visible. - A failed page does not silently advance the checkpoint. -
Reconciliation does not delete data based on incomplete sync results.

### Milestone 5 — Backend APIs, search, and architecture catalog

- Implement versioned REST APIs.
- Implement authentication and organization-scoped authorization.
- Implement search and entity detail endpoints.
- Implement relationship/neighborhood endpoints with strict limits.
- Implement Service and API catalog CRUD.
- Implement manual ownership, implementation, exposure, consumption, and
  dependency relationships.
- Record actor and provenance for manual changes.
- Add API tests.

Acceptance: - APIs return consistent validated DTOs. - Unauthorized
access is rejected. - Catalog relationships are auditable. - Search uses
indexes and pagination.

### Milestone 6 — Web portal, graph explorer, and impact analysis

- Implement a polished usable React/TypeScript portal.
- Implement connector configuration and connection validation.
- Implement sync job status/history.
- Implement search and entity details.
- Implement bounded graph neighborhood visualization.
- Implement filters and path highlighting.
- Implement architecture catalog screens.
- Implement explainable change-impact analysis.
- Show relationship provenance and source links.
- Clearly distinguish direct, indirect, historical, and inferred
  results.
- Include useful empty, loading, error, and permission-denied states.

Acceptance: - No inert UI controls. - Graph payloads are bounded. -
Users can navigate from a result to its evidence/source. - Impact
reports explain why each entity was included and state limitations.

### Milestone 7 — Read-only MCP server

- Implement the documented MCP tools.
- Reuse backend domain services.
- Enforce authenticated user and organization authorization.
- Validate inputs and apply result/depth limits.
- Return structured outputs with provenance, timestamps, source URLs,
  and caveats.
- Do not expose arbitrary Cypher execution.
- Do not expose write tools.
- Add tool contract, authorization, and integration tests.
- Document configuration for compatible clients without claiming every
  client has identical setup.

Acceptance: - MCP tools work against imported graph data. - Unauthorized
access is denied. - Tools return useful structured context. - MCP cannot
modify the graph in MVP.

### Milestone 8 — End-to-end verification and operational readiness

- Run the complete test suite.
- Add end-to-end tests covering ingestion through UI/API/MCP.
- Verify repeated sync and reconciliation.
- Verify organization isolation.
- Verify secret redaction.
- Verify Docker Compose fresh setup and restart persistence.
- Add a small clearly labeled demo fixture dataset.
- Complete setup, connector, security, architecture, graph schema, API,
  MCP, troubleshooting, backup/reset, and limitations documentation.
- Record known gaps and credential-dependent live verification steps.
- Review the whole repository for dead code, placeholders, unsafe
  defaults, and misleading claims.

Acceptance: - A new developer can follow the README to run the
application. - Demo mode is usable without external credentials. - Live
Azure DevOps setup is documented. - All available automated checks
pass. - Any unverified live integration behavior is explicitly
documented. - All eight milestone commits exist.

## 7. Architecture and coding requirements

### Backend

- Use FastAPI and typed request/response models.
- Keep routers thin.
- Separate domain services, connectors, graph repositories, and
  persistence.
- Use parameterized Cypher.
- Use explicit transaction boundaries.
- Do not share Neo4j sessions between concurrent requests.
- Validate all external data.
- Use structured logging.
- Avoid broad exception swallowing.

### Frontend

- Use TypeScript strict mode.
- Keep API calls in service modules.
- Use loading/error/empty states.
- Use accessible controls and keyboard navigation.
- Do not expose database credentials.
- Use bounded graph rendering.
- Clearly label demo data.

### Neo4j

- Install constraints and indexes automatically.
- Use stable IDs.
- Use MERGE carefully; do not overwrite properties with null values
  accidentally.
- Keep relationship semantics explicit.
- Bound variable-length traversals.
- Include organization scope in every applicable query.
- Store provenance.
- Test query behavior against real Neo4j.

### PostgreSQL

- Use migrations.
- Store job/checkpoint state transactionally.
- Do not store raw provider secrets unless a secure encryption/key
  strategy is implemented and documented.
- Use a secret reference/environment configuration for MVP.

### Azure DevOps

- Use supported APIs and handle API versioning.
- Respect pagination and continuation behavior.
- Handle throttling and retry-after responses.
- Do not assume every user has permission to every project.
- Preserve source URLs.
- Never log PATs or authorization headers.
- Document the required PAT scopes based on actual endpoints used.

### MCP

- Use the supported MCP SDK/protocol implementation.
- Validate schemas.
- Reuse authorization and domain services.
- Enforce result limits.
- Expose read-only tools only.
- Do not pass arbitrary user text into Cypher.
- Return concise structured results and provenance.

## 8. Demo mode requirements

Demo mode is required so the full application can be exercised without
external credentials.

- Include a small but realistic synthetic organization.
- Include multiple teams and repositories.
- Include work items, pull requests, commits, services, APIs, and
  cross-service relationships.
- Include provenance on relationships.
- Include at least one useful impact-analysis scenario.
- Label all demo records and UI states as demo data.
- Do not mix demo fixtures into a live organization.
- Provide a safe reset/reload command.
- Test demo mode from a clean database.

## 9. Documentation deliverables

Create and maintain: - `README.md` - `docs/implementation-plan.md` -
`docs/implementation-progress.md` -
`docs/assumptions-and-decisions.md` - `docs/architecture.md` -
`docs/graph-data-model.md` - `docs/azure-devops-setup.md` -
`docs/security.md` - `docs/api.md` - `docs/mcp-setup.md` -
`docs/development.md` - `docs/troubleshooting.md` -
`docs/known-limitations.md`

Update documentation as implementation decisions are made. Do not leave
documentation describing features that are not implemented.

## 10. Final report to user

At completion, provide: 1. What was implemented. 2. How to start the
application. 3. How to configure Azure DevOps credentials. 4. How to
load demo data. 5. How to run tests. 6. MCP configuration instructions.
7. All milestone commit hashes. 8. Test/lint/type-check results. 9.
Features that could not be live-verified due to missing credentials. 10.
Known limitations and recommended next steps.

Do not claim completion if required acceptance criteria are failing. If
something remains incomplete, state it clearly and explain what is
needed.

## 11. Final instruction

Proceed through all milestones in sequence without asking the user to
approve each one. Commit each completed milestone separately. Do not
push. Do not stop after producing a plan. Implement, test, fix,
document, and continue until the complete MVP is delivered or a genuine
external blocker prevents further progress.
