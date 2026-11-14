# EngGraph — Product Requirements Document (PRD)

**Document status:** Initial implementation baseline  
**Product:** EngGraph  
**Version:** 1.0  
**Audience:** Product owner, coding agent, engineering team

## 1. Product summary

EngGraph is an engineering intelligence platform that creates a
persistent, queryable knowledge layer across an organization’s software
engineering ecosystem.

It connects information from source control and work-management systems
so engineers and authorized AI coding agents can answer cross-system
questions without manually searching multiple tools.

The product is not intended to replace Git, Azure DevOps, code-search
tools, observability platforms, or coding agents. It complements them by
maintaining evidence-backed relationships between engineering entities.

**Primary MVP outcome:** Given an Azure DevOps work item, repository,
pull request, or service, an engineer can discover connected engineering
context such as related work items, repositories, commits, pull
requests, teams, owners, and configured service/API dependencies.

## 2. Problem statement

Engineering knowledge is distributed across repositories, work items,
pull requests, commits, teams, and architecture documentation.
Individual systems contain only part of the context.

Coding agents can inspect code and reason about a repository, but they
do not automatically maintain a consistent, permission-aware,
cross-organization model connecting work management, source control,
ownership, and service architecture.

EngGraph addresses this gap through: - Connectors that import data from
authoritative systems. - A canonical entity model. - A Neo4j graph
representing entities and their relationships. - Provenance and
synchronization metadata. - APIs and a web UI for exploration and impact
analysis. - A read-only MCP interface for compatible coding agents.

## 3. Goals

1.  Build a useful engineering knowledge graph from real Azure DevOps
    data.
2.  Connect organizations, projects, teams, users, repositories, work
    items, pull requests, commits, services, and APIs.
3.  Support repeatable, incremental synchronization without creating
    duplicates.
4.  Provide useful cross-system graph queries.
5.  Deliver an interactive web portal for exploration.
6.  Provide change-impact reports based on verified and explicitly
    inferred relationships.
7.  Expose controlled, read-only knowledge tools through MCP.
8.  Keep the architecture extensible for additional providers and future
    enterprise deployment.

## 4. Non-goals for MVP

The following are explicitly outside the MVP: - Full source-code
semantic indexing or replacing coding-agent repository analysis. -
Function-level call graph generation across arbitrary languages. -
Runtime telemetry, distributed tracing, log ingestion, or metrics
analysis. - Automated root-cause analysis. - Security vulnerability
scanning. - Autonomous code changes or write operations against source
systems. - Billing, subscriptions, and commercial SaaS administration. -
Production-grade multi-region or clustered Neo4j deployment. -
General-purpose natural-language-to-unrestricted-Cypher execution.

The architecture may leave extension points for these capabilities, but
they must not delay MVP completion.

## 5. Target users and personas

### Software engineer

Needs to understand how a work item, repository, pull request, or
service relates to other engineering assets.

### Technical lead / architect

Needs visibility into service relationships, ownership, cross-team
dependencies, and potential change impact.

### Engineering manager

Needs to understand team ownership, repository activity, and
engineering-system relationships.

### AI coding agent

Needs authorized, structured context from systems beyond the currently
opened repository.

### Platform administrator

Needs to configure integrations, credentials, synchronization, and
organization-level access.

## 6. Primary user journeys

### Journey A — Connect an organization

1.  Administrator configures an Azure DevOps organization.
2.  Administrator supplies a PAT through a secure configuration
    mechanism.
3.  Administrator selects projects and repositories to include.
4.  EngGraph validates connectivity and permissions.
5.  Administrator starts an initial synchronization.
6.  UI displays progress, errors, and imported entity counts.

### Journey B — Explore an engineering entity

1.  User searches for a work item, repository, pull request, commit,
    service, or API.
2.  User opens the entity detail page.
3.  EngGraph displays its properties, source link, related entities, and
    relationship provenance.
4.  User traverses to related entities or opens the original source
    record.

### Journey C — Analyze change impact

1.  User selects a work item, pull request, repository, service, or API.
2.  User requests an impact analysis.
3.  EngGraph traverses configured relationships.
4.  Results are grouped by direct, indirect, and inferred relationships.
5.  Each result includes the evidence/source and confidence where
    applicable.
6.  User can inspect the path explaining why an entity was included.

### Journey D — Ask an AI coding agent

1.  An authorized user configures the EngGraph MCP server.
2.  A compatible coding agent calls a specific read-only tool.
3.  EngGraph applies user and organization authorization.
4.  The tool returns structured results with source links, provenance,
    and limitations.
5.  The agent uses that information in its plan or explanation.

## 7. MVP functional requirements

### FR-01: Organization and project configuration

- Support one configured Azure DevOps organization in the initial MVP.
- Support selecting multiple Azure DevOps projects.
- Store provider configuration separately from graph entities.
- Design identifiers and data boundaries to support multiple
  organizations later.
- Do not claim production multi-tenancy in MVP.

### FR-02: Azure DevOps connector

Import, where available and authorized: - Projects. - Teams. -
Users/identities. - Git repositories. - Commits. - Pull requests. - Work
items. - Work-item relations. - Pull-request/work-item links. -
Commit/pull-request relationships when available from the provider.

The connector must use supported Azure DevOps APIs and handle
pagination, throttling, transient errors, and permission failures.

### FR-03: Canonical normalization

- Normalize provider records into canonical EngGraph entities.
- Preserve the provider name and original provider ID.
- Preserve source URLs when available.
- Preserve provider update timestamps.
- Avoid using display names as unique identifiers.
- Preserve raw source payloads only where useful and safe; do not store
  secrets.

### FR-04: Graph persistence

- Use Neo4j as the relationship store.
- Use stable IDs and uniqueness constraints.
- Use parameterized Cypher.
- Use idempotent upserts.
- Store provenance on relationships and relevant entity properties.
- Support deactivation/deletion state without destroying historical
  context unnecessarily.

### FR-05: Synchronization

- Provide an initial full synchronization.
- Provide incremental synchronization using provider update markers
  where possible.
- Support manual resynchronization.
- Record job status, start/end time, counts, and errors.
- Make jobs safe to retry.
- Provide a reconciliation strategy for missed updates and
  deleted/archived records.
- Never report a successful sync if required pages or records failed
  silently.

### FR-06: Manual architecture catalog

Because MVP does not attempt complete automatic architecture
discovery: - Allow authorized users to create and edit Service and API
catalog entities. - Allow users to associate services with
repositories. - Allow users to define service-to-service dependencies
and API consumption/implementation relationships. - Record who created
or changed each manual relationship and when. - Clearly distinguish
manually declared relationships from provider-imported or inferred
relationships.

### FR-07: Search and entity details

- Search supported entity types.
- Show entity type, name/title, source system, source link, and key
  metadata.
- Show related entities grouped by relationship type.
- Allow navigation through relationships.
- Enforce authorization before returning data.

### FR-08: Graph exploration

- Display a focused neighborhood graph for a selected entity.
- Allow filtering by node type and relationship type.
- Allow expansion of adjacent nodes with limits.
- Highlight paths between selected entities where supported.
- Avoid rendering the entire enterprise graph by default.
- Provide a useful tabular/list alternative for accessibility and large
  results.

### FR-09: Change-impact analysis

- Accept supported entity IDs as analysis subjects.
- Traverse a documented set of relationship types and bounded depths.
- Return direct and indirect results separately.
- Include the path and relationship provenance behind each result.
- Clearly label configured, observed, imported, and inferred
  relationships.
- Do not label an entity “affected” solely because it is nearby in the
  graph unless the query semantics justify that conclusion.
- Include a limitations section in every report.

### FR-10: MCP server

Provide read-only tools such as: - `search_engineering_entities` -
`get_entity_context` - `get_repository_context` -
`get_service_dependencies` - `get_change_impact` -
`get_related_work_items` - `get_component_owners`

Requirements: - Validate every argument. - Apply organization and user
authorization. - Enforce result limits and traversal-depth limits. - Use
fixed, reviewed query templates rather than unrestricted user-supplied
Cypher. - Return structured JSON with source URLs, provenance,
timestamps, and caveats. - Do not expose credentials or internal
secrets. - Do not allow graph writes through the MVP MCP interface.

### FR-11: Administration and observability

- Display configured connectors.
- Display last successful synchronization.
- Display job history and counts.
- Display actionable error messages without leaking secrets.
- Provide health checks for backend, Neo4j, PostgreSQL, and connector
  status.

## 8. Non-functional requirements

### Security

- Never commit credentials or PATs.
- Use environment variables or a documented local secret mechanism for
  development.
- Redact credentials and sensitive headers from logs.
- Enforce authorization in backend services, not only in the UI.
- Validate organization scope on every query.
- Use least-privilege provider scopes.
- Do not expose Neo4j credentials to the browser.
- MCP tools must enforce the same authorization as REST APIs.

### Reliability

- Ingestion must be idempotent.
- Failed jobs must be retryable.
- Partial failures must be visible.
- Use transaction boundaries and bounded batches.
- Handle API throttling and transient network failures.

### Performance

- Use indexes and constraints for lookup keys.
- Paginate search and entity lists.
- Bound graph traversals and graph-rendering payloads.
- Establish baseline performance tests with a synthetic dataset.
- Avoid unbounded path queries.

### Maintainability

- Use typed models and clear module boundaries.
- Keep provider-specific code out of graph domain services.
- Maintain migrations/schema initialization.
- Add automated unit, integration, and end-to-end tests.
- Document all supported relationship semantics.

### Privacy

- Import only selected projects and records needed for the product.
- Do not ingest source-code contents in MVP.
- Avoid storing unnecessary personal information.
- Support deletion/deactivation and re-synchronization policies.
- Clearly document what data is copied into EngGraph.

## 9. MVP acceptance criteria

The MVP is complete when: 1. The application starts through documented
Docker Compose commands. 2. A user can configure an Azure DevOps
connection without placing secrets in source control. 3. The connector
can enumerate selected projects and import supported records. 4.
Pagination and retry behavior are tested. 5. Re-running imports does not
create duplicate nodes or relationships. 6. Graph constraints and
indexes are installed automatically. 7. Entity details and search work
against imported data. 8. A user can create and connect Service and API
catalog entities. 9. The graph explorer renders a bounded neighborhood.
10. Change-impact analysis returns explainable paths and provenance. 11.
The MCP server exposes documented read-only tools. 12. Unauthorized
requests are rejected by backend and MCP authorization checks. 13. Demo
mode works without external credentials and is visibly identified as
demo data. 14. Automated tests pass, with known limitations documented.
15. Setup, configuration, architecture, data model, and operations
documentation are complete.

## 10. Product success measures

For the initial pilot, measure: - Time required to connect and import
one organization. - Percentage of imported records with stable source
identifiers. - Duplicate count after repeated synchronization (target:
zero). - Percentage of relationships with provenance. - Successful
completion rate of synchronization jobs. - Query latency for common
entity and impact queries. - Whether engineers can answer cross-system
questions that require multiple source systems. - Whether MCP results
are useful and correctly scoped.

Do not invent numerical performance claims before benchmarking.

## 11. Release strategy

### MVP

Azure DevOps + Git/work-management context, graph exploration, manual
service/API catalog, impact analysis, read-only MCP.

### Later releases

- Additional Git providers.
- Runtime telemetry and incident connectors.
- Historical/temporal ownership and dependency analysis.
- More sophisticated graph algorithms.
- Advanced authorization synchronization.
- Enterprise deployment options.
- Additional MCP tools and agent workflows.

## 12. Assumptions and open decisions

The coding agent should proceed using these defaults: - Product name:
EngGraph (configurable). - First provider: Azure DevOps Services. -
First deployment: local Docker Compose. - Neo4j Community for
development. - PostgreSQL for application/configuration data. -
Python/FastAPI backend. - React/TypeScript frontend. - MCP interface is
read-only. - One organization is configured for the MVP; model is
organization-scoped for future expansion. - PAT authentication is
acceptable for initial development, with secrets supplied out of band. -
No external LLM is required for core functionality. - If real
credentials are unavailable, finish and test a clearly separated demo
mode using fixtures.

The coding agent must document assumptions and proceed rather than
blocking on nonessential questions.
