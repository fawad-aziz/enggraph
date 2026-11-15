"""Manual architecture catalog (FR-06, milestone 5).

The catalog is the only place MVP users write to the graph. Rules:

- Service/API nodes are created with `sourceSystem=enggraph` and the acting
  user recorded (`createdBy`), so manual entries are distinguishable from
  provider-imported ones.
- Manual relationships are restricted to a small allowlist: OWNS,
  IMPLEMENTED_BY, EXPOSES, CONSUMES, DEPENDS_ON. Provider-derived relationship
  types are owned by sync and cannot be written here.
- Every manual edge carries `provenanceType=MANUAL`, the actor, and the
  observation time.
- Deleting a relationship is allowed only for MANUAL provenance: sync owns the
  provider-derived edges.
- Every write produces an audit record (actor, action, target).
- Endpoints must exist in the caller's organization before an edge is created.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.orm import Session

from enggraph.api.errors import ApiError
from enggraph.auth.service import Principal
from enggraph.graph.ids import api_id, service_id
from enggraph.graph.model import (
    MANUAL_SOURCE_SYSTEM,
    RELATION_SEMANTICS,
    EdgeUpsert,
    NodeUpsert,
    NodeView,
)
from enggraph.graph.repository import GraphRepository
from enggraph.jobs.queue import audit_record, redact_details
from enggraph.services.display import edge_dto, node_dto

MANUAL_REL_TYPES = ("OWNS", "IMPLEMENTED_BY", "EXPOSES", "CONSUMES", "DEPENDS_ON")
SOURCE_SYSTEM = MANUAL_SOURCE_SYSTEM
MAX_NAME_LENGTH = 200
MAX_TEXT_LENGTH = 2000


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_text(value: str, label: str, limit: int) -> str:
    text = (value or "").strip()
    if not text:
        raise ApiError("invalid_request", f"{label} must be non-empty", 400)
    if len(text) > limit:
        raise ApiError("invalid_request", f"{label} is too long", 400)
    return text


def _write_nodes(repo: GraphRepository, node: NodeUpsert) -> None:
    repo.upsert_nodes([node])


def create_service(
    session: Session,
    repo: GraphRepository,
    principal: Principal,
    *,
    name: str,
    description: str = "",
    tier: str = "",
) -> dict[str, Any]:
    clean_name = _clean_text(name, "name", MAX_NAME_LENGTH)
    clean_description = (description or "").strip()[:MAX_TEXT_LENGTH]
    clean_tier = (tier or "").strip()[:MAX_NAME_LENGTH]
    stamp = now_iso()
    node = NodeUpsert(
        label="Service",
        id=service_id(principal.organization_id, str(uuid.uuid4())),
        props={
            "name": clean_name,
            "description": clean_description,
            "tier": clean_tier,
            "sourceSystem": SOURCE_SYSTEM,
            "organizationId": principal.organization_id,
            "createdBy": principal.user_id,
            "createdAt": stamp,
            "lastSeenAt": stamp,
        },
        organization_id=principal.organization_id,
    )
    _write_nodes(repo, node)
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="catalog.service_create",
        target=node.id,
        details={"name": clean_name, "tier": clean_tier},
    )
    return node_dto(node_view(repo, node.id, principal.organization_id))


def update_service(
    session: Session,
    repo: GraphRepository,
    principal: Principal,
    *,
    service_id_value: str,
    name: str | None = None,
    description: str | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    existing = repo.get_node(service_id_value, principal.organization_id)
    if existing is None:
        raise ApiError("entity_not_found", f"entity {service_id_value} not found", 404)
    if existing.label != "Service":
        raise ApiError("entity_not_service", "only Service entities can be patched here", 400)
    props: dict[str, Any] = {"updatedAt": now_iso(), "updatedBy": principal.user_id}
    if name is not None:
        props["name"] = _clean_text(name, "name", MAX_NAME_LENGTH)
    if description is not None:
        props["description"] = description.strip()[:MAX_TEXT_LENGTH]
    if tier is not None:
        props["tier"] = tier.strip()[:MAX_NAME_LENGTH]
    if len(props) == 2:
        raise ApiError("invalid_request", "no fields to update", 400)
    repo.upsert_nodes(
        [
            NodeUpsert(
                label="Service",
                id=service_id_value,
                props=props,
                organization_id=principal.organization_id,
            )
        ]
    )
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="catalog.service_update",
        target=service_id_value,
        details=redact_details({k: v for k, v in props.items() if k != "updatedAt"}),
    )
    return node_dto(node_view(repo, service_id_value, principal.organization_id))


def create_api(
    session: Session,
    repo: GraphRepository,
    principal: Principal,
    *,
    name: str,
    method: str = "",
    path: str = "",
    description: str = "",
) -> dict[str, Any]:
    clean_name = _clean_text(name, "name", MAX_NAME_LENGTH)
    stamp = now_iso()
    node = NodeUpsert(
        label="API",
        id=api_id(principal.organization_id, str(uuid.uuid4())),
        props={
            "name": clean_name,
            "method": (method or "").strip().upper()[:16],
            "path": (path or "").strip()[:MAX_TEXT_LENGTH],
            "description": description.strip()[:MAX_TEXT_LENGTH],
            "sourceSystem": SOURCE_SYSTEM,
            "organizationId": principal.organization_id,
            "createdBy": principal.user_id,
            "createdAt": stamp,
            "lastSeenAt": stamp,
        },
        organization_id=principal.organization_id,
    )
    _write_nodes(repo, node)
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="catalog.api_create",
        target=node.id,
        details={"name": clean_name, "method": node.props["method"]},
    )
    return node_dto(node_view(repo, node.id, principal.organization_id))


def node_view(repo: GraphRepository, node_id: str, org_id: str) -> NodeView:
    """Re-read the persisted node so the DTO reflects stored state."""
    node = repo.get_node(node_id, org_id)
    if node is None:
        raise ApiError("entity_not_found", f"entity {node_id} not found", 404)
    return node


def list_label(
    repo: GraphRepository, principal: Principal, label: str, limit: int, offset: int
) -> dict[str, Any]:
    items = repo.list_by_label(label, principal.organization_id, limit, offset)
    total = repo.count_by_label(label, principal.organization_id)
    return {
        "items": [node_dto(n) for n in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def create_relationship(
    session: Session,
    repo: GraphRepository,
    principal: Principal,
    *,
    rel_type: str,
    src_id: str,
    dst_id: str,
) -> dict[str, Any]:
    if rel_type not in MANUAL_REL_TYPES:
        raise ApiError(
            "relationship_not_manual",
            f"{rel_type} is owned by synchronization; manual writes are limited to "
            f"{', '.join(MANUAL_REL_TYPES)}",
            403,
        )
    src_label, dst_label = RELATION_SEMANTICS[rel_type]
    src = repo.get_node(src_id, principal.organization_id)
    dst = repo.get_node(dst_id, principal.organization_id)
    if src is None:
        raise ApiError("entity_not_found", f"source entity {src_id} not found", 404)
    if dst is None:
        raise ApiError("entity_not_found", f"target entity {dst_id} not found", 404)
    if (src.label, dst.label) != (src_label, dst_label):
        raise ApiError(
            "invalid_endpoints",
            f"{rel_type} must connect {src_label} -> {dst_label}, got {src.label} -> {dst.label}",
            400,
        )
    stamp = now_iso()
    edge = EdgeUpsert(
        rel_type=rel_type,
        src_label=src_label,
        src_id=src_id,
        dst_label=dst_label,
        dst_id=dst_id,
        props={
            "provenanceType": "MANUAL",
            "sourceSystem": SOURCE_SYSTEM,
            "sourceRecordId": f"manual:{principal.user_id}:{rel_type}:{src_id}->{dst_id}",
            "observedAt": stamp,
            "createdBy": principal.user_id,
            "createdAt": stamp,
        },
        organization_id=principal.organization_id,
    )
    edge.validate_endpoints()
    written = repo.upsert_edges([edge])
    if written == 0:
        raise ApiError(
            "edge_not_written",
            "relationship was not written (endpoint resolution failed)",
            409,
        )
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="catalog.relationship_create",
        target=f"{src_id}-[{rel_type}]->{dst_id}",
        details={"relType": rel_type, "provenanceType": "MANUAL"},
    )
    view = repo.outgoing(src_id, principal.organization_id, [rel_type], 100)
    matching = [e for e in view if e.dst == dst_id]
    if not matching:
        raise ApiError("edge_not_written", "relationship is not present after write", 409)
    return {"subjectId": src_id, **edge_dto(matching[0])}


def delete_relationship(
    session: Session,
    repo: GraphRepository,
    principal: Principal,
    *,
    rel_type: str,
    src_id: str,
    dst_id: str,
) -> dict[str, Any]:
    if rel_type not in MANUAL_REL_TYPES:
        raise ApiError(
            "relationship_not_manual",
            f"{rel_type} is owned by synchronization; manual deletes are limited to "
            f"{', '.join(MANUAL_REL_TYPES)}",
            403,
        )
    src = repo.get_node(src_id, principal.organization_id)
    dst = repo.get_node(dst_id, principal.organization_id)
    if src is None:
        raise ApiError("entity_not_found", f"source entity {src_id} not found", 404)
    if dst is None:
        raise ApiError("entity_not_found", f"target entity {dst_id} not found", 404)
    edges = [
        e
        for e in repo.outgoing(src_id, principal.organization_id, [rel_type], 100)
        if e.dst == dst_id
    ]
    if not edges:
        raise ApiError("relationship_not_found", "relationship does not exist", 404)
    if edges[0].props.get("provenanceType") != "MANUAL":
        raise ApiError(
            "relationship_provider_owned",
            "provider-derived relationships are maintained by synchronization, "
            "not deleted manually",
            403,
        )
    removed = repo.delete_edge(rel_type, src_id, dst_id, principal.organization_id)
    if removed == 0:
        raise ApiError("relationship_not_found", "relationship does not exist", 404)
    audit_record(
        session,
        organization_id=principal.organization_id,
        actor=principal.user_id,
        action="catalog.relationship_delete",
        target=f"{src_id}-[{rel_type}]->{dst_id}",
        details={"relType": rel_type},
    )
    return {"deleted": True, "relType": rel_type, "srcId": src_id, "dstId": dst_id}


def list_manual_relationships(
    repo: GraphRepository,
    principal: Principal,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Manual edges, discovered by scanning catalog-scoped services.

    Manual relationship types are all incident to a Service node, so scanning
    services bounds the walk; provider-derived edges are filtered out.
    """
    org = principal.organization_id
    collected: list[dict[str, Any]] = []
    for service in repo.list_by_label("Service", org, limit, 0):
        for edge in repo.outgoing(service.id, org, list(MANUAL_REL_TYPES), limit):
            if edge.props.get("provenanceType") == "MANUAL":
                collected.append(edge_dto(edge))
        for edge in repo.incoming(service.id, org, ["OWNS"], limit):
            if edge.props.get("provenanceType") == "MANUAL":
                collected.append(edge_dto(edge))
    collected.sort(key=lambda e: (e["srcId"], e["relType"], e["dstId"]))
    total = len(collected)
    return {
        "items": collected[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
