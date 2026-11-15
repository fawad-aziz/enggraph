# Graph Data Model

The model implemented in `backend/enggraph/graph/model.py` and installed by
`backend/enggraph/graph/schema.py`. It matches `project-spec/03` and the code,
not a plan. Every statement here has an implementation behind it.

## Canonical identifiers

Every node has a `id` string, provider-qualified and built by
`enggraph/graph/ids.py`. Provider ids are kept separately as `sourceId`, so a
graph id never collides with a provider id from another provider, and a reader
can always tell where a node came from.

| Pattern | Meaning |
|---|---|
| `ado:{org}:project:{project_id}` | Azure DevOps project |
| `ado:{org}:team:{team_id}` | team |
| `ado:{org}:identity:{identity_id}` | developer (identity block id) |
| `ado:{org}:identity:u.{uniqueName}` | developer known only by `uniqueName` (commit authors) |
| `ado:{org}:{project}:repository:{repo_id}` | repository |
| `ado:{org}:{project}:workitem:{id}` | work item |
| `ado:{org}:{project}:pullrequest:{repo_id}:{pr_number}` | pull request |
| `ado:{org}:{project}:commit:{repo_id}:{sha}` | commit |
| `enggraph:{org}:service:{uuid}` | service created in the manual catalog |
| `enggraph:{org}:api:{uuid}` | API created in the manual catalog |
| `demo:{org}:{slug}` | demo fixture (namespaced apart from provider ids) |

`_safe()` rejects empty components and components containing `:`, so an id can
never be built from a colon-bearing value. `normalize.py`'s `source_value()`
raises when a required provider id field is absent — it does not stringify a
missing value into something that looks real.

## Labels, constraints and indexes

Ten labels, each with a uniqueness constraint on `n.id` and an index on
`n.organizationId`, all created with `IF NOT EXISTS` so installation is
repeatable (`python -m enggraph.install_schema`, run in the backend
entrypoint):

`Organization`, `Project`, `Team`, `Developer`, `Repository`, `WorkItem`,
`PullRequest`, `Commit`, `Service`, `API`.

Field indexes additionally exist on `Project.name`, `Team.name`,
`Developer.displayName`, `Repository.name`, `WorkItem.title`,
`PullRequest.title`, `Service.name`, `API.name`.

Every read filters on `organizationId`, so a query can only see one
organization's data. `NodeUpsert` / `EdgeUpsert` validate the label and the
relationship type at construction time (unknown label, unknown relationship
type, and an edge whose endpoints do not match the declared semantics all
raise), so the store is not the first place a malformed row is caught.

## Node properties

Properties common to every node:

| Property | Meaning |
|---|---|
| `id` | canonical id (unique per label) |
| `organizationId` | tenant scope (raw organization name) |
| `sourceSystem` | `azure_devops` (sync), `enggraph` (manual catalog), `demo` (fixtures) |
| `sourceId` | provider's own id, preserved |
| `sourceUrl` | provider link, where the provider gives one |
| `lastSeenAt` | UTC second-precision stamp of the pass that saw this node |
| `isDeleted` | set by reconciliation when a provider pass did not see the node |

Manual catalog nodes additionally carry `createdBy` (the acting user id),
`createdAt`, and on update `updatedBy` / `updatedAt`. Demo nodes carry
`demoData=true`, `demoNote` (stating the data is synthetic), and
`createdBy="demo-loader"`.

Label-specific properties written by the connector:

| Label | Properties |
|---|---|
| `Organization` | `name`, `provider` |
| `Project` | `name`, `abbreviation`, `description`, `state` |
| `Team` | `name`, `description` |
| `Developer` | `displayName`, `uniqueName` |
| `Repository` | `name`, `defaultBranch` |
| `WorkItem` | `title`, `state`, `reason`, `workItemType`, `priority`, `severity`, `createdDate`, `changedDate`, `createdByLabel`, `assignedToLabel` |
| `PullRequest` | `number`, `title`, `description`, `status`, `createdDate` |
| `Commit` | `hash`, `message`, `authorDate` |
| `Service` | `name`, `description`, `tier` |
| `API` | `name`, `description`, `method`, `path` |

Null provider fields are dropped before writing (`sanitized_props()`), so an
upsert never overwrites an existing value with null.

## Relationships

Directed, each with declared endpoints, and every edge carries
`provenanceType`, `sourceSystem`, `sourceRecordId`, `observedAt`.

| Type | Source → Target | Meaning |
|---|---|---|
| `HAS_PROJECT` | Organization → Project | organization contains the project |
| `HAS_TEAM` | Organization → Team | organization contains the team |
| `HAS_MEMBER` | Team → Developer | team has the developer as a member |
| `CONTAINS_REPOSITORY` | Project → Repository | project contains the repository |
| `AUTHORED` | Developer → Commit | developer authored the commit |
| `CONTAINS_COMMIT` | Repository → Commit | repository contains the commit |
| `CONTAINS_PULL_REQUEST` | Repository → PullRequest | repository contains the pull request |
| `CREATED_BY` | PullRequest → Developer | pull request was created by the developer |
| `REVIEWED_BY` | PullRequest → Developer | pull request was reviewed by the developer |
| `LINKED_TO` | PullRequest → WorkItem | pull request is linked to the work item |
| `CHANGES` | PullRequest → Commit | pull request changes the commit |
| `PARENT_OF` | WorkItem → WorkItem | work item is the parent of a child work item |
| `RELATED_TO` | WorkItem → WorkItem | work item is related to a peer work item |
| `OWNS` | Team → Service | team owns the service (catalog) |
| `IMPLEMENTED_BY` | Service → Repository | service is implemented by the repository |
| `EXPOSES` | Service → API | service exposes the API |
| `CONSUMES` | Service → API | service consumes the API |
| `DEPENDS_ON` | Service → Service | service depends on another service |

Reference-resolution rule in `normalize.py`: an edge is emitted only when both
endpoint nodes were created in the same pass, so no edge points at a node that
was never written.

The Azure DevOps connector emits the provider-derived types with
`provenanceType=PROVIDER_EXPLICIT`. The manual catalog writes `OWNS`,
`IMPLEMENTED_BY`, `EXPOSES`, `CONSUMES`, `DEPENDS_ON` with
`provenanceType=MANUAL`; provider-derived relationship types cannot be written
through the catalog, and a relationship can be deleted only when its
provenance is `MANUAL` (sync owns provider edges). `RELATED_TO` is declared in
the model and traversed by impact/work-item queries, but the MVP connector does
not emit it — demo fixtures do, so the traversal path is exercised.

## Provenance

`PROVIDER_EXPLICIT` (the provider stated it), `MANUAL` (a user configured it in
the catalog), `STATIC_ANALYSIS` (declared in code/config),
`RUNTIME_OBSERVED` (seen in runtime telemetry), `INFERRED` (derived by a
documented rule).

`EdgeUpsert.validate_endpoints()` rejects a provenance value outside this set.
In impact analysis, `INFERRED` findings come from exactly one documented rule —
services that share an API (cross-consumer) — with fixed confidence `0.6`, and
are labelled as signals for human review, not verified impact. An "explicit
ownership" claim never includes an `INFERRED` edge.

## Observation and reconciliation

- A full pass stamps `lastSeenAt` = the pass start time on every node it
  fetched. After the **complete** pass, `mark_not_seen(org, cutoff, labels)`
  marks nodes not seen in that pass as `isDeleted=true`.
- A partial pass never reconciles: unvisited provider records are not evidence
  of deletion.
- Reconciliation skips three kinds of node: demo nodes (`demoData=true`), manual
  catalog nodes (`sourceSystem=enggraph`), and nodes with no `lastSeenAt`. A
  provider pass has no evidence about records the provider never had.
- Reads exclude `isDeleted` nodes by default (search, counts, catalog listings).
- Checkpoints are per `(organization, connector, scope)` where scope is
  `project:{name}`, and `provider_version` advances only after the batch it
  describes has been persisted.

## Where writes come from

| Writer | Nodes | Edges | Provenance |
|---|---|---|---|
| Sync worker (connector) | provider-derived nodes | provider-derived types | `PROVIDER_EXPLICIT` |
| Manual catalog (`POST /api/v1/services`, `/apis`, `/relationships`) | `Service`, `API` | the 5 allowlisted types | `MANUAL` |
| Demo loader (`scripts/demo_load.py` → `enggraph/demo.py`) | all labels, ids `demo:{org}:{slug}` | provider-shaped + catalog + runtime | `PROVIDER_EXPLICIT`, `MANUAL`, `RUNTIME_OBSERVED` |

Every write from the catalog and the demo loader produces an audit record
(actor, action, target). The demo loader refuses to load into an organization
that already holds non-demo records, and `scripts/demo_reset.py` deletes only
`demoData=true` nodes in one organization, leaving the provider and manual
records and the organization's configuration row in place.
