"""Demo mode: one small, clearly labeled synthetic organization (spec §8).

Rules the code enforces:

- Demo rows are written through the *same* graph repository the sync worker
  uses (`persist_rows`, same batches, same validation), so demo mode exercises
  the production write path rather than a special one.
- Every demo node carries `demoData=true`, `sourceSystem="demo"`, `demoNote`,
  and `createdBy`, so a reader can tell demo data from provider data on the
  node itself — in the API DTO, in the MCP envelope, and in the UI badge.
- Demo nodes never carry `sourceUrl`: a synthetic record must not point at a
  real provider page.
- Demo fixtures live in their own organization. `load_demo` refuses when the
  target organization already holds provider or manual catalog records, so
  demo data is never mixed into a live organization.
- Reconciliation skips demo nodes (a provider pass has no evidence about a
  record the provider never had), and `reset_demo` removes exactly the demo
  nodes of one organization, together with the edges that touched them.

The dataset is synthetic: names, source ids, dates, and hashes are invented.
It carries no real organization's data and no credentials.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from enggraph.graph.ids import demo_id
from enggraph.graph.model import NODE_LABELS, EdgeUpsert, NodeUpsert
from enggraph.graph.repository import GraphRepository
from enggraph.jobs.queue import audit_record
from enggraph.persistence.models import OrganizationConfig
from enggraph.services.catalog import now_iso
from enggraph.sync.pipeline import persist_rows

DEMO_SOURCE_SYSTEM = "demo"
DEMO_ORG = "demo"
DEMO_ACTOR = "demo-loader"
DEMO_NOTE = "synthetic demo fixture - not a real organization"


class DemoConflictError(RuntimeError):
    """The target organization already holds non-demo records."""


def demo_dataset(org: str, observed_at: str) -> tuple[list[NodeUpsert], list[EdgeUpsert]]:
    """The synthetic organization as canonical rows (pure function, no IO).

    `observed_at` is passed in so a test can pin every `lastSeenAt`/`observedAt`
    value; production scripts pass the current time.
    """

    def node(label: str, slug: str, props: dict[str, Any]) -> NodeUpsert:
        base: dict[str, Any] = {
            "organizationId": org,
            "sourceSystem": DEMO_SOURCE_SYSTEM,
            "lastSeenAt": observed_at,
            "demoData": True,
            "demoNote": DEMO_NOTE,
            "createdBy": DEMO_ACTOR,
        }
        return NodeUpsert(
            label=label,
            id=demo_id(org, slug),
            props={**base, **props},
            organization_id=org,
        )

    def edge(
        rel: str,
        src_label: str,
        src_slug: str,
        dst_label: str,
        dst_slug: str,
        provenance: str,
        record_id: str,
    ) -> EdgeUpsert:
        return EdgeUpsert(
            rel_type=rel,
            src_label=src_label,
            src_id=demo_id(org, src_slug),
            dst_label=dst_label,
            dst_id=demo_id(org, dst_slug),
            props={
                "provenanceType": provenance,
                "sourceSystem": DEMO_SOURCE_SYSTEM,
                "sourceRecordId": record_id,
                "observedAt": observed_at,
                "createdBy": DEMO_ACTOR,
                "demoNote": DEMO_NOTE,
            },
            organization_id=org,
        )

    nodes: list[NodeUpsert] = [
        node(
            "Organization",
            "organization",
            {"name": "Demo Company (synthetic)", "provider": DEMO_SOURCE_SYSTEM},
        ),
        node(
            "Project",
            "project-checkout",
            {
                "name": "Checkout",
                "abbreviation": "CHK",
                "description": "Demo project: checkout and order placement",
                "state": "validated",
                "sourceId": "demo-src-project-checkout",
            },
        ),
        node(
            "Project",
            "project-payments",
            {
                "name": "Payments",
                "abbreviation": "PAY",
                "description": "Demo project: payment processing and refunds",
                "state": "validated",
                "sourceId": "demo-src-project-payments",
            },
        ),
        node(
            "Team",
            "team-checkout",
            {
                "name": "Checkout Squad",
                "description": "Demo team owning the checkout and order services",
                "sourceId": "demo-src-team-checkout",
            },
        ),
        node(
            "Team",
            "team-payments",
            {
                "name": "Payments Squad",
                "description": "Demo team owning the payment processing service",
                "sourceId": "demo-src-team-payments",
            },
        ),
        node(
            "Team",
            "team-platform",
            {
                "name": "Platform Squad",
                "description": "Demo team owning shared identity infrastructure",
                "sourceId": "demo-src-team-platform",
            },
        ),
        node(
            "Developer",
            "developer-ana",
            {"displayName": "Ana Demo", "uniqueName": "ana@demo.test", "sourceId": "demo-src-ana"},
        ),
        node(
            "Developer",
            "developer-omar",
            {
                "displayName": "Omar Demo",
                "uniqueName": "omar@demo.test",
                "sourceId": "demo-src-omar",
            },
        ),
        node(
            "Developer",
            "developer-li",
            {"displayName": "Li Demo", "uniqueName": "li@demo.test", "sourceId": "demo-src-li"},
        ),
        node(
            "Developer",
            "developer-sam",
            {"displayName": "Sam Demo", "uniqueName": "sam@demo.test", "sourceId": "demo-src-sam"},
        ),
        node(
            "Repository",
            "repository-checkout",
            {
                "name": "checkout-service",
                "defaultBranch": "refs/heads/main",
                "sourceId": "demo-src-repository-checkout",
            },
        ),
        node(
            "Repository",
            "repository-payments",
            {
                "name": "payments-service",
                "defaultBranch": "refs/heads/main",
                "sourceId": "demo-src-repository-payments",
            },
        ),
        node(
            "Repository",
            "repository-identity",
            {
                "name": "identity-token",
                "defaultBranch": "refs/heads/main",
                "sourceId": "demo-src-repository-identity",
            },
        ),
        node(
            "WorkItem",
            "workitem-101",
            {
                "title": "Rewrite the checkout path",
                "state": "New",
                "workItemType": "Epic",
                "priority": "High",
                "sourceId": "101",
            },
        ),
        node(
            "WorkItem",
            "workitem-110",
            {
                "title": "Split payment across methods",
                "state": "Active",
                "workItemType": "Feature",
                "sourceId": "110",
            },
        ),
        node(
            "WorkItem",
            "workitem-111",
            {
                "title": "Order service calls the payment intent API",
                "state": "Resolved",
                "workItemType": "Story",
                "sourceId": "111",
            },
        ),
        node(
            "WorkItem",
            "workitem-112",
            {
                "title": "Refund fails when the intent expires",
                "state": "New",
                "workItemType": "Bug",
                "severity": "2 (High)",
                "sourceId": "112",
            },
        ),
        node(
            "WorkItem",
            "workitem-120",
            {
                "title": "Refund path uses the payment API",
                "state": "New",
                "workItemType": "Feature",
                "sourceId": "120",
            },
        ),
        node(
            "PullRequest",
            "pullrequest-7",
            {
                "number": "7",
                "title": "Wire the order service to the payment intent API",
                "status": "completed",
                "createdDate": "2026-10-04T09:15:00Z",
                "sourceId": "7",
            },
        ),
        node(
            "PullRequest",
            "pullrequest-12",
            {
                "number": "12",
                "title": "Refund on intent expiry",
                "status": "completed",
                "createdDate": "2026-10-11T14:02:00Z",
                "sourceId": "12",
            },
        ),
        node(
            "Commit",
            "commit-aa11",
            {
                "hash": "aa11bb22cc33dd44",
                "message": "Add payment intent client to the order service",
                "authorDate": "2026-10-04T08:40:00Z",
                "sourceId": "aa11bb22cc33dd44",
            },
        ),
        node(
            "Commit",
            "commit-bb22",
            {
                "hash": "bb22cc33dd44ee55",
                "message": "Retry order placement once per intent",
                "authorDate": "2026-10-05T11:20:00Z",
                "sourceId": "bb22cc33dd44ee55",
            },
        ),
        node(
            "Commit",
            "commit-cc33",
            {
                "hash": "cc33dd44ee55ff66",
                "message": "Emit refund events on intent expiry",
                "authorDate": "2026-10-11T10:05:00Z",
                "sourceId": "cc33dd44ee55ff66",
            },
        ),
        node(
            "Commit",
            "commit-dd44",
            {
                "hash": "dd44ee55ff66aa11",
                "message": "Rotate token signing key",
                "authorDate": "2026-10-08T16:30:00Z",
                "sourceId": "dd44ee55ff66aa11",
            },
        ),
        node(
            "Service",
            "service-checkout",
            {
                "name": "Checkout Service",
                "description": "Demo service: builds the order and places it",
                "tier": "application",
                "createdAt": observed_at,
            },
        ),
        node(
            "Service",
            "service-orders",
            {
                "name": "Orders Service",
                "description": "Demo service: exposes order placement, consumes payments",
                "tier": "application",
                "createdAt": observed_at,
            },
        ),
        node(
            "Service",
            "service-payments",
            {
                "name": "Payments Service",
                "description": "Demo service: payment intents and refunds",
                "tier": "application",
                "createdAt": observed_at,
            },
        ),
        node(
            "Service",
            "service-identity",
            {
                "name": "Identity Service",
                "description": "Demo service: issues request tokens",
                "tier": "infrastructure",
                "createdAt": observed_at,
            },
        ),
        node(
            "API",
            "api-create-order",
            {
                "name": "POST /orders",
                "method": "POST",
                "path": "/orders",
                "description": "Demo API: places an order",
                "createdAt": observed_at,
            },
        ),
        node(
            "API",
            "api-payment-intent",
            {
                "name": "POST /payment-intents",
                "method": "POST",
                "path": "/payment-intents",
                "description": "Demo API: creates a payment intent",
                "createdAt": observed_at,
            },
        ),
        node(
            "API",
            "api-refund",
            {
                "name": "POST /refunds",
                "method": "POST",
                "path": "/refunds",
                "description": "Demo API: refunds a completed payment",
                "createdAt": observed_at,
            },
        ),
        node(
            "API",
            "api-token",
            {
                "name": "POST /tokens",
                "method": "POST",
                "path": "/tokens",
                "description": "Demo API: issues a request token",
                "createdAt": observed_at,
            },
        ),
    ]

    provider = "PROVIDER_EXPLICIT"
    manual = "MANUAL"
    runtime = "RUNTIME_OBSERVED"
    edges: list[EdgeUpsert] = [
        edge(
            "HAS_PROJECT",
            "Organization",
            "organization",
            "Project",
            "project-checkout",
            provider,
            "project:checkout",
        ),
        edge(
            "HAS_PROJECT",
            "Organization",
            "organization",
            "Project",
            "project-payments",
            provider,
            "project:payments",
        ),
        edge(
            "HAS_TEAM",
            "Organization",
            "organization",
            "Team",
            "team-checkout",
            provider,
            "team:checkout",
        ),
        edge(
            "HAS_TEAM",
            "Organization",
            "organization",
            "Team",
            "team-payments",
            provider,
            "team:payments",
        ),
        edge(
            "HAS_TEAM",
            "Organization",
            "organization",
            "Team",
            "team-platform",
            provider,
            "team:platform",
        ),
        edge(
            "HAS_MEMBER",
            "Team",
            "team-checkout",
            "Developer",
            "developer-ana",
            provider,
            "member:checkout:ana",
        ),
        edge(
            "HAS_MEMBER",
            "Team",
            "team-checkout",
            "Developer",
            "developer-omar",
            provider,
            "member:checkout:omar",
        ),
        edge(
            "HAS_MEMBER",
            "Team",
            "team-payments",
            "Developer",
            "developer-li",
            provider,
            "member:payments:li",
        ),
        edge(
            "HAS_MEMBER",
            "Team",
            "team-platform",
            "Developer",
            "developer-sam",
            provider,
            "member:platform:sam",
        ),
        edge(
            "CONTAINS_REPOSITORY",
            "Project",
            "project-checkout",
            "Repository",
            "repository-checkout",
            provider,
            "repository:checkout",
        ),
        edge(
            "CONTAINS_REPOSITORY",
            "Project",
            "project-payments",
            "Repository",
            "repository-payments",
            provider,
            "repository:payments",
        ),
        edge(
            "CONTAINS_REPOSITORY",
            "Project",
            "project-payments",
            "Repository",
            "repository-identity",
            provider,
            "repository:identity",
        ),
        edge(
            "AUTHORED",
            "Developer",
            "developer-ana",
            "Commit",
            "commit-aa11",
            provider,
            "author:aa11",
        ),
        edge(
            "AUTHORED",
            "Developer",
            "developer-omar",
            "Commit",
            "commit-bb22",
            provider,
            "author:bb22",
        ),
        edge(
            "AUTHORED",
            "Developer",
            "developer-li",
            "Commit",
            "commit-cc33",
            provider,
            "author:cc33",
        ),
        edge(
            "AUTHORED",
            "Developer",
            "developer-sam",
            "Commit",
            "commit-dd44",
            provider,
            "author:dd44",
        ),
        edge(
            "CONTAINS_COMMIT",
            "Repository",
            "repository-checkout",
            "Commit",
            "commit-aa11",
            provider,
            "commit:checkout:aa11",
        ),
        edge(
            "CONTAINS_COMMIT",
            "Repository",
            "repository-checkout",
            "Commit",
            "commit-bb22",
            provider,
            "commit:checkout:bb22",
        ),
        edge(
            "CONTAINS_COMMIT",
            "Repository",
            "repository-payments",
            "Commit",
            "commit-cc33",
            provider,
            "commit:payments:cc33",
        ),
        edge(
            "CONTAINS_COMMIT",
            "Repository",
            "repository-identity",
            "Commit",
            "commit-dd44",
            provider,
            "commit:identity:dd44",
        ),
        edge(
            "CONTAINS_PULL_REQUEST",
            "Repository",
            "repository-checkout",
            "PullRequest",
            "pullrequest-7",
            provider,
            "pullrequest:checkout:7",
        ),
        edge(
            "CONTAINS_PULL_REQUEST",
            "Repository",
            "repository-payments",
            "PullRequest",
            "pullrequest-12",
            provider,
            "pullrequest:payments:12",
        ),
        edge(
            "CREATED_BY",
            "PullRequest",
            "pullrequest-7",
            "Developer",
            "developer-ana",
            provider,
            "creator:7",
        ),
        edge(
            "CREATED_BY",
            "PullRequest",
            "pullrequest-12",
            "Developer",
            "developer-li",
            provider,
            "creator:12",
        ),
        edge(
            "REVIEWED_BY",
            "PullRequest",
            "pullrequest-7",
            "Developer",
            "developer-omar",
            provider,
            "reviewer:7:omar",
        ),
        edge(
            "REVIEWED_BY",
            "PullRequest",
            "pullrequest-12",
            "Developer",
            "developer-sam",
            provider,
            "reviewer:12:sam",
        ),
        edge(
            "CHANGES",
            "PullRequest",
            "pullrequest-7",
            "Commit",
            "commit-aa11",
            provider,
            "changes:7:aa11",
        ),
        edge(
            "CHANGES",
            "PullRequest",
            "pullrequest-7",
            "Commit",
            "commit-bb22",
            provider,
            "changes:7:bb22",
        ),
        edge(
            "CHANGES",
            "PullRequest",
            "pullrequest-12",
            "Commit",
            "commit-cc33",
            provider,
            "changes:12:cc33",
        ),
        edge(
            "LINKED_TO",
            "PullRequest",
            "pullrequest-7",
            "WorkItem",
            "workitem-110",
            provider,
            "link:7:110",
        ),
        edge(
            "LINKED_TO",
            "PullRequest",
            "pullrequest-7",
            "WorkItem",
            "workitem-111",
            provider,
            "link:7:111",
        ),
        edge(
            "LINKED_TO",
            "PullRequest",
            "pullrequest-12",
            "WorkItem",
            "workitem-120",
            provider,
            "link:12:120",
        ),
        edge(
            "PARENT_OF",
            "WorkItem",
            "workitem-101",
            "WorkItem",
            "workitem-110",
            provider,
            "parent:101:110",
        ),
        edge(
            "PARENT_OF",
            "WorkItem",
            "workitem-110",
            "WorkItem",
            "workitem-111",
            provider,
            "parent:110:111",
        ),
        edge(
            "PARENT_OF",
            "WorkItem",
            "workitem-110",
            "WorkItem",
            "workitem-112",
            provider,
            "parent:110:112",
        ),
        edge(
            "RELATED_TO",
            "WorkItem",
            "workitem-120",
            "WorkItem",
            "workitem-112",
            provider,
            "related:120:112",
        ),
        edge(
            "OWNS",
            "Team",
            "team-checkout",
            "Service",
            "service-checkout",
            manual,
            "catalog:owns-checkout",
        ),
        edge(
            "OWNS",
            "Team",
            "team-checkout",
            "Service",
            "service-orders",
            manual,
            "catalog:owns-orders",
        ),
        edge(
            "OWNS",
            "Team",
            "team-payments",
            "Service",
            "service-payments",
            manual,
            "catalog:owns-payments",
        ),
        edge(
            "OWNS",
            "Team",
            "team-platform",
            "Service",
            "service-identity",
            manual,
            "catalog:owns-identity",
        ),
        edge(
            "IMPLEMENTED_BY",
            "Service",
            "service-checkout",
            "Repository",
            "repository-checkout",
            manual,
            "catalog:implemented-checkout",
        ),
        edge(
            "IMPLEMENTED_BY",
            "Service",
            "service-orders",
            "Repository",
            "repository-checkout",
            manual,
            "catalog:implemented-orders",
        ),
        edge(
            "IMPLEMENTED_BY",
            "Service",
            "service-payments",
            "Repository",
            "repository-payments",
            manual,
            "catalog:implemented-payments",
        ),
        edge(
            "IMPLEMENTED_BY",
            "Service",
            "service-identity",
            "Repository",
            "repository-identity",
            manual,
            "catalog:implemented-identity",
        ),
        edge(
            "EXPOSES",
            "Service",
            "service-orders",
            "API",
            "api-create-order",
            manual,
            "catalog:exposes-orders",
        ),
        edge(
            "EXPOSES",
            "Service",
            "service-payments",
            "API",
            "api-payment-intent",
            manual,
            "catalog:exposes-intent",
        ),
        edge(
            "EXPOSES",
            "Service",
            "service-payments",
            "API",
            "api-refund",
            manual,
            "catalog:exposes-refund",
        ),
        edge(
            "EXPOSES",
            "Service",
            "service-identity",
            "API",
            "api-token",
            manual,
            "catalog:exposes-token",
        ),
        edge(
            "CONSUMES",
            "Service",
            "service-checkout",
            "API",
            "api-create-order",
            manual,
            "catalog:consumes-orders",
        ),
        edge(
            "CONSUMES",
            "Service",
            "service-checkout",
            "API",
            "api-payment-intent",
            manual,
            "catalog:consumes-intent",
        ),
        edge(
            "CONSUMES",
            "Service",
            "service-orders",
            "API",
            "api-token",
            manual,
            "catalog:consumes-token",
        ),
        edge(
            "DEPENDS_ON",
            "Service",
            "service-checkout",
            "Service",
            "service-orders",
            runtime,
            "runtime:checkout-orders",
        ),
        edge(
            "DEPENDS_ON",
            "Service",
            "service-orders",
            "Service",
            "service-payments",
            runtime,
            "runtime:orders-payments",
        ),
        edge(
            "DEPENDS_ON",
            "Service",
            "service-payments",
            "Service",
            "service-identity",
            runtime,
            "runtime:payments-identity",
        ),
    ]
    return nodes, edges


def organization_rows_for(repo: GraphRepository, org: str) -> int:
    """Nodes in `org` that a provider pass or the manual catalog owns.

    Demo rows are ignored. Bounded: one node per label is enough to decide.
    """
    total = 0
    for label in NODE_LABELS:
        for node in repo.list_by_label(label, org, 1):
            if node.props.get("demoData") is not True:
                total += 1
    return total


def load_demo(
    repo: GraphRepository,
    session: Session,
    *,
    org: str = DEMO_ORG,
    actor: str = DEMO_ACTOR,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Load the demo dataset into `org` through the production graph repository.

    Refuses when `org` already holds provider or catalog records. Returns the
    write counts plus the post-load graph counts.
    """
    conflicts = organization_rows_for(repo, org)
    if conflicts:
        raise DemoConflictError(
            f"organization {org!r} already holds {conflicts} non-demo records; "
            "demo fixtures must not be mixed into a live organization. "
            f"Load demo data into a separate organization (--org {DEMO_ORG})."
        )
    stamp = observed_at or now_iso()
    nodes, edges = demo_dataset(org, stamp)
    nodes_written, edges_written = persist_rows(repo, nodes, edges)
    config = session.execute(
        select(OrganizationConfig).where(OrganizationConfig.organization_id == org)
    ).scalar_one_or_none()
    if config is None:
        # `id` is the primary key with no default generator, so it is supplied.
        config = OrganizationConfig(
            id=str(uuid.uuid4()),
            organization_id=org,
            display_name=f"{org} (demo data)",
            max_depth=4,
        )
        session.add(config)
    elif not config.display_name:
        config.display_name = f"{org} (demo data)"
    session.commit()
    audit_record(
        session,
        organization_id=org,
        actor=actor,
        action="demo.load",
        target=org,
        details={"nodes_written": nodes_written, "edges_written": edges_written, "demo_data": True},
    )
    return {
        "organizationId": org,
        "nodesWritten": nodes_written,
        "edgesWritten": edges_written,
        "counts": repo.counts(org),
        "demoData": True,
    }


def reset_demo(
    repo: GraphRepository,
    session: Session,
    *,
    org: str = DEMO_ORG,
    actor: str = DEMO_ACTOR,
) -> dict[str, Any]:
    """Remove every demo node in `org`, and the edges that touched them.

    Non-demo records in the organization are untouched, so this is safe to run
    against an organization that also holds provider data.
    """
    removed = repo.delete_demo(org)
    audit_record(
        session,
        organization_id=org,
        actor=actor,
        action="demo.reset",
        target=org,
        details={"nodes_removed": removed, "demo_data": True},
    )
    return {
        "organizationId": org,
        "nodesRemoved": removed,
        "counts": repo.counts(org),
        "demoData": True,
    }
