"""Ingestion persistence: bounded batches, checkpoints, reconciliation.

Pipeline stages (architecture doc §2.4): fetch -> validate/retain source ids
-> normalize -> resolve references -> upsert nodes -> upsert relationships ->
record checkpoint and counts -> reconcile. Every stage is observable through
job events, and bounded by `BATCH_SIZE`. No DB transaction is held open
during remote API calls: fetch happens first, then persistence, then the
checkpoint advance.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from enggraph.graph.model import NODE_LABELS, EdgeUpsert, NodeUpsert
from enggraph.graph.repository import GraphRepository
from enggraph.jobs.queue import audit_record, new_id
from enggraph.persistence.models import SyncCheckpoint, utcnow

BATCH_SIZE = 100


def scope_for(project: str) -> str:
    """Checkpoint scope key for one project in one connector."""
    return f"project:{project}"


def chunked(items: list[Any], size: int = BATCH_SIZE) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def persist_rows(
    repo: GraphRepository,
    nodes: list[NodeUpsert],
    edges: list[EdgeUpsert],
) -> tuple[int, int]:
    """Write bounded batches; returns (nodes written, edges written)."""
    nodes_written = 0
    for batch in chunked(nodes):
        nodes_written += repo.upsert_nodes(batch)
    edges_written = 0
    for batch in chunked(edges):
        edges_written += repo.upsert_edges(batch)
    return nodes_written, edges_written


def get_checkpoint(
    session: Session, org_id: str, connector_id: str, scope: str
) -> SyncCheckpoint | None:
    return session.execute(
        select(SyncCheckpoint).where(
            SyncCheckpoint.organization_id == org_id,
            SyncCheckpoint.connector_config_id == connector_id,
            SyncCheckpoint.scope == scope,
        )
    ).scalar_one_or_none()


def advance_checkpoint(
    session: Session,
    *,
    org_id: str,
    connector_id: str,
    scope: str,
    job_id: str,
    provider_version: int | None = None,
    last_seen_at: str | None = None,
    full_pass: bool = False,
) -> SyncCheckpoint:
    """Advance a checkpoint after the page/batch it describes is persisted.

    `provider_version` is only supplied once that batch has been written, so a
    failed page never advances the position past unprocessed data.
    """
    checkpoint = get_checkpoint(session, org_id, connector_id, scope)
    if checkpoint is None:
        checkpoint = SyncCheckpoint(
            id=new_id(),
            organization_id=org_id,
            connector_config_id=connector_id,
            scope=scope,
            provider_version=0,
        )
        session.add(checkpoint)
    if provider_version is not None:
        checkpoint.provider_version = provider_version
    if last_seen_at is not None:
        checkpoint.last_seen_at = last_seen_at
    if full_pass:
        checkpoint.last_full_pass_at = utcnow()
        checkpoint.last_full_pass_job_id = job_id
    checkpoint.updated_at = utcnow()
    session.commit()
    return checkpoint


def reconcile(
    session: Session,
    repo: GraphRepository,
    *,
    org_id: str,
    cutoff_iso: str,
    actor: str,
    job_id: str,
) -> int:
    """Reconciliation: mark nodes not seen in this pass.

    Called only after a complete pass. A partial pass must not mark anything
    deleted, because unvisited provider records are not evidence of deletion.
    """
    marked = repo.mark_not_seen(org_id, cutoff_iso, list(NODE_LABELS))
    audit_record(
        session,
        organization_id=org_id,
        actor=actor,
        action="sync.reconcile",
        target=job_id,
        details={"cutoff": cutoff_iso, "marked_not_seen": marked},
    )
    return marked
