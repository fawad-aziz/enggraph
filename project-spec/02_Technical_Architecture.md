# EngGraph — Technical Architecture

**Document status:** Initial implementation baseline  
**Version:** 1.0

## 1. Architecture principles

1.  Source systems remain authoritative.
2.  Neo4j stores a connected projection of engineering entities and
    relationships.
3.  PostgreSQL stores application configuration, users, connector
    settings metadata, job state, and operational records.
4.  Provider-specific integrations are isolated behind connector
    interfaces.
5.  All imported entities use stable, provider-qualified identifiers.
6.  Every relationship has explicit semantics and provenance.
7.  Ingestion is idempotent and retryable.
8.  Authorization is enforced in backend services and MCP tools.
9.  The UI and MCP server consume the same domain/query services.
10. The MVP favors explainable, bounded queries over unrestricted graph
    access.

## 2. Logical components

### 2.1 Frontend

React + TypeScript application.

Responsibilities: - Organization and connector configuration. - Sync job
status and history. - Entity search and detail pages. - Graph
neighborhood visualization. - Service/API catalog management. -
Change-impact reports. - Authentication/session handling.

Suggested libraries: - React Router. - TanStack Query. - Cytoscape.js
for graph visualization. - A consistent component library selected by
the coding agent.

### 2.2 Backend API

Python + FastAPI.

Responsibilities: - Authentication and authorization. - REST
endpoints. - Domain services. - Search and graph query orchestration. -
Connector administration. - Sync job management. - Service/API catalog
operations. - Impact analysis. - Audit logging.

The API layer must not contain provider-specific parsing logic or large
Cypher strings. Delegate to domain services and graph repositories.

### 2.3 Connector subsystem

Define a provider-neutral connector contract.

Suggested conceptual interface:

``` python
class EngineeringConnector(Protocol):
    def validate_connection(self) -> ConnectionResult: ...
    def list_projects(self) -> list[ExternalProject]: ...
    def fetch_work_items(self, scope, cursor=None) -> Page: ...
    def fetch_repositories(self, scope, cursor=None) -> Page: ...
    def fetch_pull_requests(self, scope, cursor=None) -> Page: ...
    def fetch_commits(self, scope, cursor=None) -> Page: ...
```

The exact interface should reflect actual provider API capabilities. Do
not fabricate unsupported endpoint behavior.

Initial implementation: - Azure DevOps Services connector. - Git
metadata imported through Azure DevOps where available. - Optional local
Git inspection only if it adds a real MVP capability and is documented
separately.

### 2.4 Ingestion pipeline

Stages: 1. Fetch external records. 2. Validate and retain source
identifiers. 3. Normalize to canonical domain models. 4. Resolve
references between imported records. 5. Upsert nodes. 6. Upsert
relationships. 7. Record checkpoint and counts. 8. Reconcile
stale/deleted records where provider semantics allow.

Each stage must be observable and retryable. Use bounded batches. Avoid
holding a database transaction open during remote API calls.

### 2.5 Graph repository layer

Owns: - Neo4j driver lifecycle. - Schema installation. - Constraints and
indexes. - Parameterized Cypher templates. - Transaction management. -
Graph persistence and reads. - Bounded traversals.

Use official Neo4j Python Driver. Use `execute_read` and `execute_write`
transaction functions where appropriate. Do not share sessions across
concurrent requests.

### 2.6 PostgreSQL

Use for: - Application users and roles. - Organization configuration. -
Connector configuration metadata. - Secret references (not raw secrets
unless encrypted with a clear key-management design). - Sync jobs and
checkpoints. - Audit records. - UI preferences.

Neo4j remains the store for connected engineering entities and
relationships.

### 2.7 MCP server

Expose a separate MCP server or a clearly isolated MCP application
module.

It must call the same domain services as the REST API. It must not
create a second implementation of business logic.

MVP tools are read-only and narrowly scoped. Use reviewed query
templates. No arbitrary Cypher execution through the product-facing MCP
tools.

## 3. Deployment topology

Local development uses Docker Compose:

- `frontend`
- `backend`
- `worker`
- `mcp-server`
- `neo4j`
- `postgres`

Optional: - `redis` for job queue/cache if the selected implementation
requires it.

Prefer the simplest reliable background-job implementation. If a queue
is used, document persistence and retry behavior. Do not add Redis
merely for architectural decoration.

Persist Neo4j and PostgreSQL data in named volumes. Provide health
checks and startup dependency handling.

## 4. Data flow

### Initial synchronization

1.  Admin configures provider connection and selected projects.
2.  Backend validates credentials.
3.  Admin starts an import job.
4.  Worker fetches paginated records.
5.  Connector converts responses into canonical records.
6.  Normalizer resolves IDs and references.
7.  Graph repository upserts entities and relationships.
8.  PostgreSQL records job progress and checkpoints.
9.  UI shows completion, partial failures, and imported counts.

### Incremental synchronization

1.  Worker loads last successful checkpoint.
2.  Connector fetches changed records using provider-supported filters
    or continuation markers.
3.  Normalizer compares provider timestamps/versions.
4.  Graph updates changed properties and relationships.
5.  Checkpoint advances only after successful processing of the relevant
    page/batch.
6.  Reconciliation detects stale or deleted records where supported.

A failed page must not cause the checkpoint to skip unprocessed data.

### User query

1.  User authenticates.
2.  API resolves organization and access scope.
3.  Domain service validates query parameters.
4.  Graph repository runs bounded, parameterized Cypher.
5.  Results are filtered to authorized scope.
6.  API returns canonical DTOs with provenance and source URLs.

### MCP query

Same authorization and domain-service flow as REST. MCP must not bypass
permissions.

## 5. Identity and data modeling rules

Use stable IDs, not names.

Suggested identifier format:

``` text
ado:{organization}:{project}:workitem:{id}
ado:{organization}:{project}:repository:{repository_id}
ado:{organization}:{project}:pullrequest:{repository_id}:{pull_request_id}
ado:{organization}:{project}:commit:{repository_id}:{commit_hash}
ado:{organization}:team:{team_id}
ado:{organization}:identity:{identity_id}
enggraph:{organization}:service:{stable_uuid}
enggraph:{organization}:api:{stable_uuid}
```

Normalize case only where the provider defines identifiers as
case-insensitive. Preserve original display values.

Every imported entity should include where applicable: - `id` -
`sourceSystem` - `sourceId` - `sourceUrl` - `organizationId` -
`createdAt` - `updatedAt` - `lastSeenAt` - `syncStatus` - `isDeleted` or
lifecycle state

Do not store access tokens or credentials in graph nodes.

## 6. Provenance and confidence

Relationships should carry: - `sourceSystem` or source mechanism. -
`sourceRecordId` or evidence reference. - `observedAt`. - `validFrom`
and `validTo` when temporal history is supported. - `confidence` only
when the relationship is inferred. - `relationshipStatus`.

Recommended provenance categories: - `PROVIDER_EXPLICIT`: explicit
source-system relationship. - `STATIC_ANALYSIS`: derived from source
code. - `RUNTIME_OBSERVED`: derived from telemetry. - `MANUAL`: entered
by an authorized user. - `INFERRED`: inferred by a rule or algorithm.

For MVP, most relationships will be `PROVIDER_EXPLICIT` or `MANUAL`. Do
not manufacture confidence scores for explicit relationships.

## 7. Authorization and security

### MVP authorization

Implement: - Authenticated application users. - Organization-scoped
access. - Admin and read-only roles at minimum. - Connector
administration restricted to admins. - Graph queries scoped to the
user’s authorized organization. - MCP authorization identical to API
authorization.

If source-system permissions cannot be fully mirrored in MVP, document
this limitation prominently. Do not imply that source-level access
control is synchronized if it is not.

### Secrets

- Read PAT from environment or secure local secret configuration.
- Never commit `.env` files containing credentials.
- Include `.env.example` with placeholders only.
- Redact authorization headers and tokens from logs.
- Document least-privilege PAT scopes.
- Never return secrets from API endpoints.

### Network and database access

- Do not expose Neo4j or PostgreSQL publicly by default.
- Browser communicates with backend, not databases.
- Use parameterized Cypher.
- Apply request limits and graph traversal limits.
- Avoid arbitrary Cypher execution from user input.

## 8. API design

Use versioned REST endpoints, for example `/api/v1`.

Suggested endpoint groups:

### Organizations and connectors

- `GET /api/v1/organizations`
- `GET /api/v1/connectors`
- `POST /api/v1/connectors/azure-devops/validate`
- `POST /api/v1/connectors/azure-devops/configure`
- `GET /api/v1/connectors/azure-devops/projects`

### Synchronization

- `POST /api/v1/sync-jobs`
- `GET /api/v1/sync-jobs`
- `GET /api/v1/sync-jobs/{job_id}`
- `POST /api/v1/sync-jobs/{job_id}/retry`

### Search and entities

- `GET /api/v1/search?q=...&types=...`
- `GET /api/v1/entities/{entity_id}`
- `GET /api/v1/entities/{entity_id}/relationships`
- `GET /api/v1/entities/{entity_id}/neighborhood`

### Architecture catalog

- `POST /api/v1/services`
- `PATCH /api/v1/services/{id}`
- `POST /api/v1/apis`
- `POST /api/v1/relationships`
- `DELETE /api/v1/relationships/{id}`

### Analysis

- `POST /api/v1/impact-analysis`
- `GET /api/v1/impact-analysis/{analysis_id}`

Exact endpoint shapes may be refined during implementation, but maintain
consistent response envelopes, validation, pagination, and error
semantics.

## 9. MCP tool contracts

Initial tools:

### `search_engineering_entities`

Inputs: query, entity types, limit.  
Output: matching canonical entities with IDs, types, source links, and
concise metadata.

### `get_entity_context`

Inputs: entity ID, relationship types, depth, limit.  
Output: entity plus bounded connected context and provenance.

### `get_repository_context`

Inputs: repository ID.  
Output: repository metadata, linked commits/PRs/work items, associated
services, and known owners.

### `get_service_dependencies`

Inputs: service ID, direction, depth, limit.  
Output: dependency paths with relationship semantics and provenance.

### `get_change_impact`

Inputs: entity ID, analysis scope, depth, limit.  
Output: direct and indirect candidate impacts, paths, evidence, and
limitations.

### `get_related_work_items`

Inputs: entity ID, limit.  
Output: linked work items with source links and relationship paths.

### `get_component_owners`

Inputs: entity ID.  
Output: explicitly configured owners and any separately labeled inferred
contributors.

MCP must not expose a generic `run_cypher` tool in the MVP.

## 10. Graph query safety and performance

- Every query must have bounded traversal depth.
- Every list query must have a result limit.
- Index stable IDs and frequently searched fields.
- Use uniqueness constraints.
- Avoid unbounded variable-length paths.
- Avoid returning entire nodes with unnecessary properties.
- Return path explanations when useful, but cap path count.
- Add query tests using representative graph fixtures.
- Profile slow queries and add indexes based on actual query plans.

## 11. Testing architecture

### Unit tests

- Normalization.
- ID construction.
- Relationship mapping.
- Authorization decisions.
- Query input validation.
- Provenance generation.

### Connector tests

- Mock provider responses.
- Pagination.
- Throttling and retry.
- Missing/partial fields.
- Permission errors.
- Continuation/checkpoint behavior.

### Graph integration tests

Run against a real Neo4j test instance/container: - Constraints and
indexes. - Idempotent upserts. - Relationship creation and updates. -
Deletion/deactivation. - Traversal correctness. - Organization
isolation.

### API and MCP tests

- Authenticated and unauthenticated requests.
- Cross-organization access denial.
- Result limits.
- Tool schema validation.
- Read-only enforcement.

### End-to-end tests

- Start services.
- Load fixtures.
- Search entities.
- Explore graph.
- Run impact analysis.
- Call MCP tools.
- Verify results and provenance.

## 12. Operational requirements

- Structured logs with correlation IDs.
- Health/readiness endpoints.
- Sync job metrics and counts.
- Clear error categories.
- No secrets in logs.
- Database backup/restore instructions for local deployment.
- Database schema initialization and versioning.
- Document how to reset demo data safely.
- Document how to reset a development environment.

## 13. Future extension points

Keep interfaces extensible for: - GitHub connector. - Additional Azure
DevOps organizations. - OpenTelemetry traces and service maps. -
Incident-management systems. - Deployment platforms. - Cloud
infrastructure inventories. - Temporal graph history. - Neo4j Graph Data
Science. - Additional MCP tools.

Do not implement these future integrations unless required for a
complete MVP or explicitly authorized.
