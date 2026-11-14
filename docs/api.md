# EngGraph REST API

Base URL: `http://localhost:8000` (port from `BACKEND_PORT`). Every route lives
under `/api/v1`. OpenAPI JSON is served by FastAPI at `/openapi.json` and the
interactive docs at `/docs`.

## Conventions

- **JSON, camelCase** in request bodies, query aliases, and responses.
- **Every response carries `x-correlation-id`** — taken from the request header
  if present, otherwise generated. It is the key for server-side log lookup.
- **Timestamps** are UTC, `YYYY-MM-DDTHH:MM:SSZ`.
- **Entity ids** are the canonical ids from `docs/graph-data-model.md`
  (e.g. `ado:acme:Commerce:repository:r-uuid-1`). They appear as `entityId`,
  `srcId`, `dstId`, `subjectId`.

### Authentication

All routes except `/api/v1/health`, `/api/v1/health/ready` and `/api/v1/auth/login`
require `Authorization: Bearer <token>`, where the token is the value returned by
`POST /auth/login`. Sessions expire after `ENGGRAPH_SESSION_TTL_HOURS` (12 by
default); the store keeps only a SHA-256 digest of the token.

- Missing header / non-bearer scheme → `401 missing_token`.
- Forged, expired, revoked, or inactive-user sessions → `401 invalid_token`,
  always with the same message (`session is invalid or expired`), so the API
  does not disclose which state the session is in.

### Authorization (server-side)

- **Roles**: `admin` and `viewer`. Writes (connector configure, sync enqueue/
  retry/cancel, catalog create/patch/delete, user provisioning) require
  `admin` → `403 admin_required` for a viewer.
- **Organization scope**: the organization a caller may read comes from the
  authenticated principal. There is no `organizationId` request parameter
  anywhere; supplying one is ignored (bodies reject unknown fields).
- **Cross-organization reads are 404, not 403** (`entity_not_found`,
  `job_not_found`, `analysis_not_found`, `connector_not_found`): an outside
  caller cannot confirm that a resource exists in another organization.
- MCP tools (milestone 7, not built yet) are planned to resolve the same
  bearer token and enforce the same rules; the MCP layer will be read-only.

### Bounds

| Bound | Default | Ceiling | Applies to |
|---|---|---|---|
| `depth` | 4 (`DEFAULT_DEPTH`) | `ENGGRAPH_MAX_DEPTH` (4), request range 1–10 | neighborhood, paths, impact analysis |
| `limit` | 50 (`DEFAULT_LIMIT`) | `ENGGRAPH_MAX_RESULTS` (200), request range 1–1000 | search, lists, relationships, neighborhoods, paths |
| `offset` | 0 | range 0–100000 | search, list endpoints |
| free-text query | — | 120 chars (`MAX_QUERY_LENGTH`) | `q`, `nodeTypes`, `relTypes` |
| `to` target id | — | 160 chars | `/entities/{id}/paths` |

Values above the ceiling are **clamped**, not rejected, and the response reports
the effective `depth`/`limit`. Traversal is bounded in the repository query
itself; there is no unbounded variable-length path.

### Pagination and truncation

List responses are `{items, total, limit, offset}`: `total` is the count matching
the filter, so a client can page. Traversal responses (`relationships`,
`neighborhood`, `paths`, impact reports) include `truncated: true` when the
result set hit a bound — a truncated report never presents itself as complete.

### Error envelope

```json
{"error": {"code": "entity_not_found", "message": "entity ado:acme:x not found", "correlationId": "a1b2c3d4e5f6"}}
```

Messages never contain credentials, connection strings, stack details, or
provider secrets; connector failures are redacted before they reach the
response. Unexpected failures log in full server-side and return
`500 internal_error` with a generic message.

| Code | Status | Meaning |
|---|---|---|
| `invalid_request` | 400 | validation failure (body, query, field length) |
| `invalid_entity_type` / `invalid_relationship_type` | 400 | filter not in the known label/relationship set |
| `invalid_category` | 400 | impact category not in `DIRECT/DEPENDENCY/HISTORICAL/OWNERSHIP/INFERRED` |
| `invalid_role` | 400 | role not in `admin/viewer` |
| `missing_token` | 401 | no bearer token |
| `invalid_token` | 401 | session forged/expired/revoked |
| `invalid_credentials` | 401 | login failed (one message, no account enumeration) |
| `admin_required` | 403 | viewer role on a write endpoint |
| `relationship_not_manual` | 403 | write/delete attempted on a provider-owned relationship |
| `relationship_provider_owned` | 403 | delete attempted on a sync-derived edge |
| `entity_not_found` / `job_not_found` / `connector_not_found` / `analysis_not_found` | 404 | not in the caller's organization (also the cross-org answer) |
| `invalid_endpoints` | 400 | relationship endpoints do not match the type's semantics |
| `job_not_retryable` / `job_not_cancellable` | 409 | job state does not allow the transition |
| `edge_not_written` | 409 | edge failed endpoint resolution, or is absent after write |
| `connector_failure` | 502 | provider-side failure (auth, permission, throttle, 5xx) |
| `graph_unavailable` | 503 | graph store not connected |
| `internal_error` | 500 | unhandled failure |

---

## Health

| Route | Auth | Response |
|---|---|---|
| `GET /api/v1/health` | none | `{"status":"ok","version":"0.1.0","mode":"demo"}` — process liveness only |
| `GET /api/v1/health/ready` | none | `200` when all components are ok and config is clean; `503` otherwise |

Readiness body:

```json
{
  "status": "degraded",
  "components": {"backend": "ok", "postgres": "ok", "neo4j": "unreachable"},
  "config_problems": ["ENGGRAPH_SESSION_SECRET is required (session signing)"]
}
```

`config_problems` names missing/placeholder configuration (live-mode secret,
placeholder DSN, missing PAT/organization, empty Neo4j password) without
printing any value.

## Auth

| Route | Role | Request | Response |
|---|---|---|---|
| `POST /api/v1/auth/login` | — | `{"email","password"}` | `{"token","userId","email","role","organizationId","sessionId"}` |
| `GET /api/v1/auth/me` | any | — | same principal shape, no token |
| `POST /api/v1/auth/logout` | any | — | `{"revoked":true}` (current session revoked) |
| `POST /api/v1/auth/users` | admin | `{"email","password","role":"viewer"}` → `201` | `{"userId","email","role","organizationId","isActive"}` |

Passwords must be ≥ 8 characters (`invalid_request` otherwise). Roles are
validated against `admin`/`viewer`. New users are created inside the admin's
own organization. Login failures answer
`{"code":"invalid_credentials","message":"invalid email or password"}` for both
unknown email and wrong password.

## Organizations

`GET /api/v1/organizations` (any role) returns exactly one entry — the caller's
organization:

```json
{"items":[{"organizationId":"acme","displayName":"Acme","maxDepth":4,
           "graphCounts":{"Repository":3,"Developer":2},"mode":"demo"}],
 "total":1,"limit":1,"offset":0}
```

## Connectors

| Route | Role | Request | Response |
|---|---|---|---|
| `POST /api/v1/connectors/azure-devops/validate` | admin | `{"adoOrganization","patSecretRef":"AZURE_DEVOPS_PAT"}` | `{"organization","reachable":true,"sampleProjectNames":[...],"secretReference"}` |
| `POST /api/v1/connectors/azure-devops/configure` | admin | `{"adoOrganization","patSecretRef","projectNames":[...],"pageSize":100}` → `201` | connector DTO (below) |
| `GET /api/v1/connectors/azure-devops/projects` | any | — | `{"items":[{"name","id","state"}],"total","limit","offset"}` |

`patSecretRef` is an **environment variable name** (validated as such). The
secret value is resolved at call time, never stored, never logged, never
returned. The DTO exposes only the reference:

```json
{"id":"cfg-uuid","organizationId":"acme","provider":"azure_devops",
 "adoOrganization":"acme","patSecretRef":"AZURE_DEVOPS_PAT",
 "projectNames":["Commerce"],"pageSize":100,"isActive":true,
 "lastValidatedAt":"2026-11-14T10:00:00Z","lastValidationOk":true}
```

Validation makes exactly **one** request to `/_apis/projects` (first page,
10 projects) — it answers "do these credentials reach this organization" and
does not scale with organization size. It raises the typed connector errors:
401 → `ConnectorAuthError`, 403 → `ConnectorPermissionError`, 429 past the
retry budget → `ConnectorThrottled`, transport/5xx past the retry budget →
`ConnectorUnavailable`, all surfaced as `connector_failure` (502). A missing
secret reference is a server configuration problem → `connector_failure` (503).
`GET .../projects` hits the live API through the same client (no cache), and
`404 connector_not_configured` when the organization has no active connector.
Up to 200 project names may be configured (`MAX_PROJECT_NAMES`).

## Sync jobs

| Route | Role | Request | Response |
|---|---|---|---|
| `POST /api/v1/sync-jobs` | admin | `{"connectorConfigId","mode":"FULL"\|"INCREMENTAL"}` → `201` | job progress DTO |
| `GET /api/v1/sync-jobs?limit&offset` | any | — | `{items:[job DTO], total, limit, offset}`, newest first |
| `GET /api/v1/sync-jobs/{jobId}` | any | — | job progress DTO |
| `POST /api/v1/sync-jobs/{jobId}/retry` | admin | — → `201` | new queued job, `parentJobId` set |
| `POST /api/v1/sync-jobs/{jobId}/cancel` | admin | — | canceled job DTO |

Job progress DTO:

```json
{"jobId":"job-uuid","organizationId":"acme","connectorConfigId":"cfg-uuid",
 "mode":"FULL","state":"RUNNING","attempt":1,"parentJobId":null,
 "workerId":"worker-1","nodesWritten":14,"edgesWritten":17,
 "projectsCompleted":1,"projectsPlanned":1,"error":null,
 "requestedBy":"user-uuid","createdAt":"...","startedAt":"...","finishedAt":null,
 "events":[{"seq":1,"stage":"fetch","level":"INFO","message":"project Commerce fetched"}]}
```

`state` is one of `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELED`.
Enqueue is admin-only because the job writes into the graph store. A retry is a
**new** job linked to the failed parent (history is kept, never rewritten).
`retry` on a job that is not `FAILED`/`CANCELED` → `409 job_not_retryable`;
`cancel` on a finished job → `409 job_not_cancellable`. Job events are recorded per stage and the
`details` of audit records are redacted.

## Search

```
GET /api/v1/search?q=cache&types=Service,API&limit=50&offset=0
```

- `q` ≤ 120 chars, `types` = comma-separated node labels (empty = all labels;
  an unknown label → `invalid_entity_type`).
- Response: `{items:[node DTO], total, limit, offset, query, types}` where
  `total` is the count matching the query, so `offset += limit` pages.
- The repository query is parameterized and scoped by `organizationId`, with
  `LIMIT`/`SKIP` applied.

## Entities

| Route | Request | Response |
|---|---|---|
| `GET /api/v1/entities/{entityId}` | — | node DTO |
| `GET /api/v1/entities/{entityId}/relationships?limit` | — | `{items:[{direction:"outgoing"\|"incoming", ...edge DTO}], total, limit, subjectId, truncated}` |
| `GET /api/v1/entities/{entityId}/neighborhood?depth&limit&nodeTypes&relTypes` | — | `{subject, nodes:[node DTO], edges:[edge DTO], depth, limit, filters:{nodeTypes,relTypes}, truncated}` |
| `GET /api/v1/entities/{entityId}/paths?to&depth&limit&relTypes` | — | `{from,to,depth,limit,paths:[{entities,relationships}],truncated}` |

Node DTO (used by search, catalog, entity, impact):

```json
{"entityId":"ado:acme:Commerce:repository:r-uuid-1","entityType":"Repository",
 "name":"commerce-api","sourceSystem":"azure_devops","sourceId":"r-uuid-1",
 "sourceUrl":"https://dev.azure.com/acme/Commerce/_git/commerce-api",
 "lastSeenAt":"2026-11-14T10:00:00Z","isDemoData":false,
 "properties":{"defaultBranch":"main", ...}}
```

Edge DTO:

```json
{"relType":"CONTAINS_REPOSITORY","srcId":"ado:acme:project:p-uuid-1",
 "dstId":"ado:acme:Commerce:repository:r-uuid-1","provenanceType":"PROVIDER",
 "sourceSystem":"azure_devops","sourceRecordId":"r-uuid-1",
 "observedAt":"2026-11-14T10:00:00Z","createdBy":null,"properties":{...}}
```

`relTypes`/`nodeTypes` must be known labels/types; the walk is bounded by
`depth` and `limit` and `truncated` says whether the bound was consumed.
`isDemoData` marks fixture records.

## Catalog (manual architecture)

Reads (any role):

| Route | Response |
|---|---|
| `GET /api/v1/services?limit&offset` | `{items:[node DTO], total, limit, offset}` |
| `GET /api/v1/apis?limit&offset` | same shape |
| `GET /api/v1/relationships?limit&offset` | `{items:[edge DTO], total, limit, offset}` — MANUAL edges only |

Writes (admin only; each write produces an audit record):

| Route | Request | Notes |
|---|---|---|
| `POST /api/v1/services` → `201` | `{"name","description","tier"}` | node stamped `sourceSystem=enggraph`, `createdBy` |
| `PATCH /api/v1/services/{serviceId}` | `{"name","description","tier"}` (subset) | Service label required; empty patch → `400` |
| `POST /api/v1/apis` → `201` | `{"name","method","path","description"}` | `method` uppercased, ≤ 16 chars |
| `POST /api/v1/relationships` → `201` | `{"relType","srcId","dstId"}` | allowlist below; endpoints must exist in the caller's org and match the type semantics |
| `DELETE /api/v1/relationships?relType&srcId&dstId` | query aliases camelCase | MANUAL provenance only |

Manual relationship allowlist: `OWNS`, `IMPLEMENTED_BY`, `EXPOSES`,
`CONSUMES`, `DEPENDS_ON`. Provider-derived types (`LINKED_TO`, `CHANGES`, …)
are owned by synchronization: writing them → `403 relationship_not_manual`,
deleting them → `403 relationship_provider_owned`. Wrong endpoint labels →
`invalid_endpoints` (e.g. `EXPOSES` must be `Service` → `API`). Every manual
edge carries `provenanceType=MANUAL`, `sourceSystem=enggraph`,
`sourceRecordId="manual:<userId>:<rel>:<src>-><dst>"`, `observedAt`,
`createdBy`.

Name limits: 200 chars for names/tiers, 2000 for descriptions/paths.

## Impact analysis

```
POST /api/v1/impact-analysis   → 201
{
  "subjectId": "ado:acme:Commerce:repository:r-uuid-1",
  "categories": ["DIRECT","DEPENDENCY","OWNERSHIP","INFERRED"],
  "depth": 4,
  "limit": 200
}
```

- `subjectId` must be non-empty and exist in the caller's organization
  (`404` otherwise); `depth`/`limit` are positive and clamped to the
  configured ceilings; `categories` must be from the known set.
- Response (the persisted report, also returned by
  `GET /api/v1/impact-analysis/{analysisId}`):

```json
{
  "analysisId": "uuid", "organizationId": "acme",
  "subjectId": "...", "subjectType": "Repository", "subjectName": "commerce-api",
  "sourceUrl": "https://dev.azure.com/...", "requestedBy": "user-uuid",
  "categories": [...], "depth": 4, "limit": 200, "createdAt": "...",
  "counts": {"DIRECT":5,"DEPENDENCY":2,"HISTORICAL":3,"OWNERSHIP":1,"INFERRED":1},
  "findings": [
    {"category":"DEPENDENCY","entityId":"...","entityType":"Service",
     "name":"commerce-ui","sourceUrl":null,"depth":2,"confidence":1.0,
     "provenanceTypes":["PROVIDER","MANUAL"],"hasManualEvidence":true,
     "lastSeenAt":"...","path":{"entities":[...],"relationships":[...]}}
  ],
  "truncated": false,
  "limitations": ["Traversal is bounded to depth 4 and 200 entities per category; ..."]
}
```

Findings are ordered by category, then depth, then entity id, deduped per
`(category, entity)`. Each finding names the **path witness** (entity list plus
the relationships connecting them) and the provenance types on that path — the
report explains *why* an entity is included; proximity alone is never treated
as impact. `INFERRED` findings come from one documented rule (services sharing
an API) with fixed confidence `0.6` and are labelled as signals for human
review. Every report includes a `limitations` section naming the bounds in
effect, the last-sync caveat, catalog coverage, and (when applicable)
truncation. Reports are persisted organization-scoped; `GET` outside that
organization → `404 analysis_not_found`. POST is available to any authenticated
role (read-only against the graph; the persisted report is an analysis record).

---

## Contract tests

`backend/tests/test_api_*.py` exercise the full request path (auth,
authorization, bounds, catalog writes, connector validation, sync enqueue,
impact analysis) against an in-memory graph engine, an in-memory SQL store, and
an HTTP-transport fixture for Azure DevOps — no Neo4j, Postgres, or live
credentials. `tests/api_support.py` builds the app with those seams. The
production app (`uvicorn enggraph.main:app`) uses the live driver and Postgres;
the test seams are never wired into `build_runtime_app()`.
