# Azure DevOps Setup (live mode)

The connector is a real `https://dev.azure.com/{organization}` REST client
(`backend/enggraph/connectors/azure_devops/`): PAT Basic auth, pinned
`api-version=7.2`, continuation-token and `searchCriteria[skip]` paging,
`429` honoured via `Retry-After`, bounded retries, typed errors for
auth/permission/throttle/unavailable. It is not a mock.

**Not exercised here.** No Azure DevOps credentials exist in the development
environment used for this build, so no live call has been made against a real
organization. The connector is exercised against 13 recorded payload fixtures
served by `httpx.MockTransport`, which pins the request shapes, paging,
throttle, and error handling. The live verification steps are listed at the end.

## 1. Create a read-only PAT

Azure DevOps → user settings → **Personal access tokens** → New token.
The connector performs only reads, so grant only read scopes:

| Scope | Why the connector needs it |
|---|---|
| **Code (Repos) → Read** | `GET /{project}/_apis/git/repositories`, `.../repositories/{id}/pullrequests`, `.../repositories/{id}/commits`, `.../pullRequests/{n}/workItemReferences` |
| **Work Items → Read** | `POST /{project}/_apis/wit/wiql`, `GET /{project}/_apis/wit/workitems` (bulk), `GET /{project}/_apis/wit/changes` (incremental) |
| **Project and Management → Read** | `GET /_apis/projects`, `GET /{project}/_apis/projects/{project}/teams`, `.../teams/{id}/members` |

Nothing write-capable is required, and nothing in the code writes to Azure
DevOps. Set the token's "authorized organization" access to the single
organization, leave "Grant this PAT full access" **unchecked**, and set an
expiry you intend to honour.

Supplied out-of-band. A PAT never goes in source, tests, logs, screenshots,
commits, issues, or PR descriptions. `.env` files that contain real secrets are
gitignored; `.env.example` holds placeholders only.

## 2. Environment

```dotenv
ENGGRAPH_MODE=live
AZURE_DEVOPS_PAT=<the PAT>            # gitignored, never committed
AZURE_DEVOPS_ORGANIZATION=<org name>
```

`ENGGRAPH_MODE=live` makes `validate_runtime()` require the PAT, the
organization, the session secret, and a Postgres DSN without `CHANGE_ME`.
Readiness reports those problems; the MCP process refuses to start on them,
and the demo scripts exit 2.

The organization name is validated by `services_base_url()`: empty, or
containing `: / ? space tab newline`, raises a configuration error, so the
base URL cannot be pointed at another host.

## 3. Configure the connector through the API (admin role required)

`POST /api/v1/connectors/azure-devops/validate`

```json
{ "adoOrganization": "contoso", "patSecretRef": "AZURE_DEVOPS_PAT" }
```

Exactly one live request is made (`GET /_apis/projects?$top=10`) — validation
answers "do these credentials reach this organization" and returns the first
page's project names. Paging the full list here would cost up to
`MAX_COLLECTION_PAGES` requests for no extra signal. `POST /api/v1/connectors/azure-devops/configure`
runs the same validation first, then stores the row and writes an audit record.

`ConnectorConfig.pat_secret_ref` stores the **name** of the environment
variable, not the PAT. `AZURE_DEVOPS_PAT` (a settings alias) is checked first,
so a `.env`-provided PAT works; any other reference is resolved from the
environment by `enggraph/secrets.py` at call time. The response DTO exposes the
reference, never the value; the value is never logged (log redaction strips
`authorization`/`pat`/`token`/`secret` fields).

`GET /api/v1/connectors/azure-devops/projects` lists projects live from the
caller's active connector. There is no cached or fake project list.

## 4. What a full sync fetches

Per configured project (`sync/worker.py` → `fetch_project_graph`):

| Endpoint | Data |
|---|---|
| `GET /_apis/projects` (`$top=page_size`) | project list; configured names must be present, else the job fails naming the project |
| `GET /{project}/_apis/git/repositories` | repositories |
| `GET /{project}/_apis/projects/{project}/teams` | teams |
| `GET /{project}/_apis/projects/{project}/teams/{id}/members` | team members (identity → `Developer`) |
| `GET /{project}/_apis/git/repositories/{id}/pullrequests` (`status=completed`, skip-paged) | pull requests, capped at `MAX_PRS_PER_REPO = 200` |
| `GET /{project}/_apis/git/repositories/{id}/commits` (`includeDetails=true`, skip-paged) | commits, capped at `MAX_COMMITS_PER_REPO = 500` |
| `GET /{project}/_apis/git/repositories/{id}/pullRequests/{n}/workItemReferences` | PR ↔ work item links |
| `POST /{project}/_apis/wit/wiql` (`SELECT [System.Id] FROM WorkItems`) | work item ids |
| `GET /{project}/_apis/wit/workitems?ids=...` | work items in chunks of `BULK_CHUNK_SIZE = 100` (ADO bulk limit) |

Incremental jobs read the per-project checkpoint version and call
`GET /{project}/_apis/wit/changes?version={v}&top={page_size}`, fetch the
changed work items, persist, then advance the checkpoint to `nextVersion`. A
failed page leaves the checkpoint where it was, so the next run re-fetches the
unprocessed range. Reconciliation runs only after a complete full pass.

Bounds in one place: `page_size` (configurable, default 100, stored with the
connector), `MAX_COLLECTION_PAGES = 200` per collection, `max_retries = 3`,
backoff capped at 30 s, `429` → `Retry-After` (default 1 s when the header is
absent). 401/403 raise typed errors immediately and are never retried. A
repeated continuation token raises rather than spinning.

## 5. Running the sync

```bash
# one queued job, then exit
python -m enggraph.worker --once
# daemon loop (compose `worker` service runs this)
python -m enggraph.worker --poll-seconds 5
```

Queue a job with `POST /api/v1/sync-jobs` (admin), watch it with
`GET /api/v1/sync-jobs/{id}` — each pipeline stage appends an event
(`start`, `fetch:projects`, `persist:{project}`, `changes:{project}`,
`reconcile`, `complete`, `failed`), and messages are redacted before storage.
Retry is `POST /api/v1/sync-jobs/{id}/retry` (a new job linked to the failed
parent, `attempt` incremented).

## 6. Live verification (not run in this environment)

With credentials in place, this sequence exercises the connector end to end:

1. `GET /api/v1/health/ready` → `status: ok` (Neo4j, Postgres reachable, no
   config problems).
2. `POST /api/v1/connectors/azure-devops/validate` → `reachable: true` plus
   sample project names.
3. `POST /api/v1/connectors/azure-devops/configure` with 1–2 small projects.
4. `POST /api/v1/sync-jobs` (`mode: FULL`), poll `GET /api/v1/sync-jobs/{id}`
   until `SUCCEEDED`, read `nodesWritten` / `edgesWritten` /
   `projectsCompleted`.
5. `GET /api/v1/search?q=...` and `GET /api/v1/entities/{id}/neighborhood` →
   provider nodes with `sourceSystem: azure_devops` and `sourceUrl` links back
   to ADO.
6. `POST /api/v1/sync-jobs` again (`mode: INCREMENTAL`) → the checkpoint
   version advances and the event log names `changes:{project}`.
7. `GET /api/v1/sync-jobs/{id}` after a full pass → the `reconcile` event's
   `marked_not_seen` count, cross-checked against `GET /api/v1/organizations`.

Steps 1–7 are documented as required and un-performed here; the equivalent
behaviour is proven in tests against recorded payloads
(`tests/test_ado_client.py`, `tests/test_ado_normalize.py`,
`tests/test_sync_worker.py`, `tests/test_end_to_end.py`).

## 7. Failure modes the connector names

- `authentication failed (401): PAT invalid or expired`
- `access denied (403): PAT lacks required scope for this resource`
- `throttled past retry budget for {path}`
- `transport failure after N retries`
- `paging exceeded 200 pages for {path}` / `continuation token repeated for {path}`
- `project {name} is not in the organization`
- `secret reference 'X' is not set in the environment`

A job failure records the typed error name and message in the job's `error`
column (redacted, capped at 2000 chars) and in a `failed` event, with
`projects_completed` preserved and checkpoints untouched.
