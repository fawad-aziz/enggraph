"""Sync worker: claims jobs from Postgres and runs the ingestion pipeline.

Full pass: fetch projects -> per project fetch graph -> persist bounded
batches -> advance checkpoint -> reconcile (complete passes only).

Incremental pass: per project read checkpoint, fetch changed work items from
the Changes API, persist, then advance the checkpoint to `nextVersion`.

A failed page leaves the checkpoint where it was, so the next run re-fetches
the unprocessed range. Reconciliation only runs on a complete pass.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable

from sqlalchemy.orm import Session

from enggraph.config import Settings
from enggraph.connectors.azure_devops.client import AdoClient
from enggraph.connectors.azure_devops.connector import (
    fetch_changes,
    fetch_project_graph,
    fetch_projects,
    fetch_work_items,
)
from enggraph.connectors.azure_devops.normalize import work_item_rows
from enggraph.connectors.errors import ConnectorConfigError, ConnectorError
from enggraph.graph.repository import GraphRepository
from enggraph.jobs.queue import (
    audit_record,
    claim_next_job,
    complete_job,
    fail_job,
    record_event,
)
from enggraph.persistence.models import ConnectorConfig, SyncJob, utcnow
from enggraph.secrets import resolve_secret
from enggraph.sync.pipeline import (
    advance_checkpoint,
    get_checkpoint,
    persist_rows,
    reconcile,
    scope_for,
)

ClientFactory = Callable[[ConnectorConfig], AdoClient]


def iso_utc(dt: datetime.datetime) -> str:
    """ISO-8601 UTC second precision, matching graph `lastSeenAt` values."""
    return dt.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def production_client_factory(settings: Settings) -> ClientFactory:
    """Production entry point: PAT resolved from its reference, never from the DB.

    `pat_secret_ref` names an environment variable. The settings alias
    (`AZURE_DEVOPS_PAT`) is checked first so a `.env`-provided PAT works;
    any other reference is read straight from the environment. The resolved
    value is never stored, logged, or returned by an endpoint.
    """

    def factory(connector: ConnectorConfig) -> AdoClient:
        ref = connector.pat_secret_ref
        if ref == "AZURE_DEVOPS_PAT":
            pat = settings.azure_devops_pat
            if not pat:
                raise ConnectorConfigError(
                    f"secret reference {ref!r} is not set in the environment"
                )
        else:
            pat = resolve_secret(ref)
        return AdoClient(connector.ado_organization, pat, page_size=connector.page_size)

    return factory


def run_job(
    session: Session, job: SyncJob, *, client_factory: ClientFactory, repo: GraphRepository
) -> SyncJob:
    """Run one claimed job. Returns the job in its final state."""
    connector = session.get(ConnectorConfig, job.connector_config_id)
    if connector is None:
        return fail_job(session, job, "connector configuration is missing", projects_completed=0)
    org = connector.ado_organization
    if job.organization_id != org:
        # Graph writes are org-scoped; a mismatch would write into the wrong tenant.
        return fail_job(
            session,
            job,
            f"job organization {job.organization_id} does not match "
            f"connector organization {org}",
            projects_completed=0,
        )
    started = job.started_at or utcnow()
    observed_at = iso_utc(started)
    projects = list(connector.project_names)
    job.projects_planned = len(projects)
    session.commit()
    record_event(session, job, "start", f"mode={job.mode} projects={len(projects)}")

    nodes_written = 0
    edges_written = 0
    completed = 0
    client: AdoClient | None = None
    try:
        client = client_factory(connector)
        if job.mode == "FULL":
            available = {p.get("name"): p for p in fetch_projects(client)}
            record_event(session, job, "fetch:projects", f"available={len(available)}")
            for name in projects:
                raw = available.get(name)
                if raw is None:
                    raise ConnectorError(f"project {name!r} is not in the organization")
                nodes, edges = fetch_project_graph(client, org, raw, observed_at)
                n, e = persist_rows(repo, nodes, edges)
                nodes_written += n
                edges_written += e
                advance_checkpoint(
                    session,
                    org_id=org,
                    connector_id=connector.id,
                    scope=scope_for(name),
                    job_id=job.id,
                    last_seen_at=observed_at,
                    full_pass=True,
                )
                completed += 1
                record_event(session, job, f"persist:{name}", f"nodes={n} edges={e}")
            marked = reconcile(
                session,
                repo,
                org_id=org,
                cutoff_iso=observed_at,
                actor="system",
                job_id=job.id,
            )
            record_event(session, job, "reconcile", f"marked_not_seen={marked}")
        else:
            for name in projects:
                checkpoint = get_checkpoint(session, org, connector.id, scope_for(name))
                version = checkpoint.provider_version if checkpoint else 0
                record_event(session, job, f"changes:{name}", f"since_version={version}")
                body = fetch_changes(client, name, version)
                ids = [c["itemId"] for c in body.get("changes", []) if c.get("itemId") is not None]
                raw_items = fetch_work_items(client, name, ids) if ids else []
                nodes, edges = work_item_rows(org, name, raw_items, observed_at)
                n, e = persist_rows(repo, nodes, edges)
                nodes_written += n
                edges_written += e
                next_version = int(body.get("nextVersion", version))
                advance_checkpoint(
                    session,
                    org_id=org,
                    connector_id=connector.id,
                    scope=scope_for(name),
                    job_id=job.id,
                    provider_version=next_version,
                    last_seen_at=observed_at,
                )
                completed += 1
                record_event(
                    session,
                    job,
                    f"persist:{name}",
                    f"nodes={n} edges={e} version={next_version}",
                )
    except ConnectorError as exc:
        message = f"{type(exc).__name__}: {exc}"
        record_event(session, job, "failed", message, level="ERROR")
        return fail_job(session, job, message, projects_completed=completed)
    except Exception as exc:  # unexpected provider/pipeline failure
        message = f"{type(exc).__name__}: {exc}"
        record_event(session, job, "failed", message, level="ERROR")
        return fail_job(session, job, message, projects_completed=completed)
    finally:
        if client is not None:
            client.close()

    complete_job(
        session,
        job,
        nodes_written=nodes_written,
        edges_written=edges_written,
        projects_completed=completed,
    )
    record_event(
        session,
        job,
        "complete",
        f"nodes={nodes_written} edges={edges_written} projects={completed}",
    )
    audit_record(
        session,
        organization_id=org,
        actor=job.requested_by or "system",
        action="sync.complete",
        target=job.id,
        details={"nodes": nodes_written, "edges": edges_written, "projects": completed},
    )
    return job


def run_once(
    session: Session, worker_id: str, *, client_factory: ClientFactory, repo: GraphRepository
) -> SyncJob | None:
    """Claim one job and run it. Returns None when the queue is empty."""
    job = claim_next_job(session, worker_id)
    if job is None:
        return None
    return run_job(session, job, client_factory=client_factory, repo=repo)
