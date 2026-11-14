# EngGraph — Neo4j Graph Data Model

**Document status:** Initial implementation baseline  
**Version:** 1.0

## 1. Modeling objectives

The graph should answer cross-system engineering questions. It should
not attempt to duplicate every detail of the source systems or recreate
a complete source-code abstract syntax tree.

Model the stable engineering entities and the relationships that connect
them.

Design principles: - Use explicit labels and relationship types. - Use
stable, provider-qualified IDs. - Use directed relationships with clear
semantics. - Keep source-system provenance. - Distinguish explicit,
manual, observed, and inferred relationships. - Scope all entities to an
organization. - Avoid generic `RELATED_TO` relationships when a
meaningful type exists. - Keep MVP schema small and extensible.

## 2. MVP node labels

### Organization

Properties: - `id` — unique stable ID. - `name` - `provider` -
`createdAt` - `updatedAt`

### Project

- `id`
- `name`
- `sourceSystem`
- `sourceUrl`
- `createdAt`
- `updatedAt`

### Team

- `id`
- `name`
- `sourceSystem`
- `sourceUrl`
- `createdAt`
- `updatedAt`

### Developer

- `id`
- `displayName`
- `email` only when permitted and necessary.
- `sourceSystem`
- `sourceUrl`
- `active`

Avoid using email as the primary identifier.

### Repository

- `id`
- `name`
- `defaultBranch`
- `remoteUrl`
- `sourceSystem`
- `sourceUrl`
- `createdAt`
- `updatedAt`
- `isArchived`

### WorkItem

- `id`
- `title`
- `workItemType`
- `state`
- `areaPath`
- `iterationPath`
- `sourceSystem`
- `sourceUrl`
- `createdAt`
- `updatedAt`

### PullRequest

- `id`
- `title`
- `status`
- `sourceSystem`
- `sourceUrl`
- `createdAt`
- `updatedAt`
- `closedAt`

### Commit

- `id`
- `hash`
- `message`
- `committedAt`
- `sourceSystem`
- `sourceUrl`

### Service

- `id`
- `name`
- `description`
- `criticality`
- `lifecycleStatus`
- `createdAt`
- `updatedAt`
- `createdBy`

Service is a manually managed architecture-catalog entity in MVP unless
a reliable source provides it.

### API

- `id`
- `name`
- `version`
- `protocol`
- `description`
- `sourceUrl`
- `createdAt`
- `updatedAt`

API is a manually managed architecture-catalog entity in MVP.

## 3. MVP relationship types

Use these directions consistently:

| Relationship            | Direction                | Meaning                                                           |
|-------------------------|--------------------------|-------------------------------------------------------------------|
| `HAS_PROJECT`           | Organization → Project   | Organization contains project                                     |
| `HAS_TEAM`              | Organization → Team      | Organization contains team                                        |
| `HAS_MEMBER`            | Team → Developer         | Developer belongs to team                                         |
| `CONTAINS_REPOSITORY`   | Project → Repository     | Project contains repository                                       |
| `AUTHORED`              | Developer → Commit       | Developer authored commit                                         |
| `CONTAINS_COMMIT`       | Repository → Commit      | Commit belongs to repository                                      |
| `CONTAINS_PULL_REQUEST` | Repository → PullRequest | PR belongs to repository                                          |
| `CREATED_BY`            | PullRequest → Developer  | PR creator                                                        |
| `REVIEWED_BY`           | PullRequest → Developer  | PR reviewer                                                       |
| `LINKED_TO`             | PullRequest → WorkItem   | PR linked to work item                                            |
| `CHANGES`               | PullRequest → Commit     | Commit included in PR, when source data supports it               |
| `PARENT_OF`             | WorkItem → WorkItem      | Work item hierarchy                                               |
| `RELATED_TO`            | WorkItem → WorkItem      | Explicit provider relation; preserve relation subtype as property |
| `OWNS`                  | Team → Service           | Team owns service                                                 |
| `IMPLEMENTED_BY`        | Service → Repository     | Repository implements service                                     |
| `EXPOSES`               | Service → API            | Service exposes API                                               |
| `CONSUMES`              | Service → API            | Service consumes API                                              |
| `DEPENDS_ON`            | Service → Service        | Service dependency                                                |

Do not create a relationship if the source data does not support it. For
example, don’t infer PR authorship from commit authorship if the
provider does not expose the relationship.

## 4. Relationship properties and provenance

Where applicable, relationships should include:

``` text
sourceSystem
sourceRecordId
provenanceType
observedAt
validFrom
validTo
confidence
notes
```

Guidance: - `provenanceType` should be one of `PROVIDER_EXPLICIT`,
`MANUAL`, `STATIC_ANALYSIS`, `RUNTIME_OBSERVED`, or `INFERRED`. -
`confidence` is optional and should only be set for inferred
relationships with a documented scoring rule. - `validFrom` and
`validTo` support historical validity. For MVP, current relationships
may have `validFrom` and null `validTo`. - `observedAt` records when
EngGraph observed the relationship. - `sourceRecordId` points to the
external evidence record when applicable.

For manually created relationships, store actor and timestamps. Do not
silently convert manual relationships into provider-derived ones.

## 5. Stable identifier strategy

Use a canonical `id` property on every node.

Examples:

``` text
ado:{organization}:project:{project_id}
ado:{organization}:team:{team_id}
ado:{organization}:identity:{identity_id}
ado:{organization}:{project}:repository:{repository_id}
ado:{organization}:{project}:workitem:{work_item_id}
ado:{organization}:{project}:pullrequest:{repository_id}:{pull_request_id}
ado:{organization}:{project}:commit:{repository_id}:{commit_hash}
enggraph:{organization}:service:{uuid}
enggraph:{organization}:api:{uuid}
```

Important: - Encode or normalize components safely. - Preserve original
provider IDs separately. - Do not use titles, names, email addresses, or
mutable paths as identity. - Ensure organization/project scope is
included where provider IDs are not globally unique.

## 6. Constraints and indexes

Create uniqueness constraints for each label’s `id`.

Illustrative Cypher (adapt syntax to the selected Neo4j version):

``` cypher
CREATE CONSTRAINT organization_id IF NOT EXISTS
FOR (n:Organization) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT project_id IF NOT EXISTS
FOR (n:Project) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT team_id IF NOT EXISTS
FOR (n:Team) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT developer_id IF NOT EXISTS
FOR (n:Developer) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT repository_id IF NOT EXISTS
FOR (n:Repository) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT workitem_id IF NOT EXISTS
FOR (n:WorkItem) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT pullrequest_id IF NOT EXISTS
FOR (n:PullRequest) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT commit_id IF NOT EXISTS
FOR (n:Commit) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT service_id IF NOT EXISTS
FOR (n:Service) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT api_id IF NOT EXISTS
FOR (n:API) REQUIRE n.id IS UNIQUE;
```

Add indexes for frequently searched fields, such as repository name,
work item title, PR title, service name, and API name. Choose exact
index types based on actual query requirements and Neo4j version.

If uniqueness constraints on a label are not supported in the target
deployment/version, use the appropriate supported constraint form and
test it at startup.

## 7. Example graph

Conceptual sample:

``` text
(Organization: Acme)
  -[:HAS_PROJECT]->
(Project: Commerce)
  -[:CONTAINS_REPOSITORY]->
(Repository: commerce-api)

(Repository)
  -[:CONTAINS_PULL_REQUEST]->
(PullRequest: Add identity provider support)
  -[:LINKED_TO]->
(WorkItem: Support multiple identity providers)

(Team: Platform)
  -[:OWNS]->
(Service: Identity Service)
  -[:IMPLEMENTED_BY]->
(Repository: identity-service)

(Service: Commerce API)
  -[:CONSUMES]->
(API: Identity API v2)

(Service: Identity Service)
  -[:EXPOSES]->
(API: Identity API v2)
```

The graph can answer cross-system questions by traversing these explicit
relationships.

## 8. Example Cypher queries

### Find repositories in a project

``` cypher
MATCH (p:Project {id: $project_id})
      -[:CONTAINS_REPOSITORY]->(r:Repository)
RETURN r.id, r.name, r.sourceUrl
ORDER BY r.name;
```

### Find work items linked to a pull request

``` cypher
MATCH (pr:PullRequest {id: $pull_request_id})
      -[:LINKED_TO]->(w:WorkItem)
RETURN w.id, w.title, w.state, w.sourceUrl;
```

### Find owners of a service

``` cypher
MATCH (t:Team)-[:OWNS]->(s:Service {id: $service_id})
RETURN t.id, t.name;
```

### Find APIs consumed by a service

``` cypher
MATCH (s:Service {id: $service_id})-[:CONSUMES]->(a:API)
RETURN a.id, a.name, a.version;
```

### Find services that consume APIs exposed by a target service

``` cypher
MATCH (target:Service {id: $service_id})-[:EXPOSES]->(api:API)
MATCH (consumer:Service)-[:CONSUMES]->(api)
RETURN DISTINCT consumer.id, consumer.name, api.name;
```

### Find a bounded dependency path

``` cypher
MATCH p=(start:Service {id: $service_id})-[:DEPENDS_ON*1..4]->(downstream:Service)
RETURN p
LIMIT 100;
```

### Find work items linked to repositories through pull requests

``` cypher
MATCH (r:Repository {id: $repository_id})
      -[:CONTAINS_PULL_REQUEST]->(pr:PullRequest)
      -[:LINKED_TO]->(w:WorkItem)
RETURN DISTINCT w.id, w.title, w.state, w.sourceUrl
ORDER BY w.id;
```

All application queries must use parameters. The examples are for
learning and should be converted into reviewed query templates.

## 9. Impact analysis semantics

Do not treat every reachable node as an impacted node.

Define separate categories: - **Directly linked:** explicit relationship
to the selected entity. - **Dependency candidate:** reachable through an
approved dependency relationship. - **Historically related:** linked
through commits, PRs, or work items. - **Ownership context:** team or
developer associated with the entity. - **Inferred:** derived by a
documented rule and clearly labeled.

Each result should include: - Entity ID and type. - Relationship path. -
Relationship types. - Provenance. - Source URLs. - Confidence if
inferred. - Explanation of why it was included.

The impact query must use an explicit allowlist of traversable
relationship types and a bounded maximum depth.

## 10. Organization isolation

Every query must be scoped to the authorized organization.

Recommended pattern: - Every top-level entity has an `organizationId`
property. - IDs include organization/provider scope where necessary. -
Every query receives the authorized organization ID from the
authenticated server context. - Do not trust an organization ID supplied
only by the browser or MCP caller. - Test that cross-organization
queries cannot leak data.

Even if MVP configures one organization, implement the scope checks now.

## 11. Data lifecycle and synchronization

Track: - Last successful source synchronization. - Last observed source
update time. - Last seen time. - Current/deleted/archived status. -
Source version or revision where available.

Do not delete graph nodes merely because a partial sync did not return
them. Deletion decisions must be based on a complete, authoritative
reconciliation or explicit provider deletion signal.

For historical records, preserve identity and relevant relationships
when appropriate. Document retention policy.

## 12. Schema evolution

- Version graph schema changes.
- Keep schema installation/migration scripts in source control.
- Make migrations repeatable where possible.
- Test migrations against an existing graph.
- Avoid destructive migrations without a backup and explicit migration
  plan.
- Document relationship semantic changes.

## 13. Future graph extensions

Possible future labels: - `Incident` - `Deployment` - `Environment` -
`Database` - `DatabaseTable` - `CloudResource` - `Trace` - `Endpoint` -
`SecurityFinding` - `ArchitectureDecision`

Possible future relationships: - `DEPLOYED_AS` - `RUNS_IN` - `CALLS` -
`READS_FROM` - `WRITES_TO` - `OBSERVED_IN` - `INVOLVED_IN` -
`INTRODUCED_BY` - `RESOLVED_BY` - `SUPERSEDES`

Do not add these to MVP without a real source and tested use case.
