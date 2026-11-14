"""Canonical EngGraph domain model: labels, relationship semantics, provenance."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, field_validator

# Node labels from the graph data model (spec §2)
NODE_LABELS: tuple[str, ...] = (
    "Organization",
    "Project",
    "Team",
    "Developer",
    "Repository",
    "WorkItem",
    "PullRequest",
    "Commit",
    "Service",
    "API",
)

# Directed relationship semantics (spec §3): type -> (source label, target label)
RELATION_SEMANTICS: dict[str, tuple[str, str]] = {
    "HAS_PROJECT": ("Organization", "Project"),
    "HAS_TEAM": ("Organization", "Team"),
    "HAS_MEMBER": ("Team", "Developer"),
    "CONTAINS_REPOSITORY": ("Project", "Repository"),
    "AUTHORED": ("Developer", "Commit"),
    "CONTAINS_COMMIT": ("Repository", "Commit"),
    "CONTAINS_PULL_REQUEST": ("Repository", "PullRequest"),
    "CREATED_BY": ("PullRequest", "Developer"),
    "REVIEWED_BY": ("PullRequest", "Developer"),
    "LINKED_TO": ("PullRequest", "WorkItem"),
    "CHANGES": ("PullRequest", "Commit"),
    "PARENT_OF": ("WorkItem", "WorkItem"),
    "RELATED_TO": ("WorkItem", "WorkItem"),
    "OWNS": ("Team", "Service"),
    "IMPLEMENTED_BY": ("Service", "Repository"),
    "EXPOSES": ("Service", "API"),
    "CONSUMES": ("Service", "API"),
    "DEPENDS_ON": ("Service", "Service"),
}

ProvenanceType = Literal[
    "PROVIDER_EXPLICIT", "MANUAL", "STATIC_ANALYSIS", "RUNTIME_OBSERVED", "INFERRED"
]


class NodeUpsert(BaseModel):
    """Idempotent node write keyed on canonical `id`."""

    label: str
    id: str
    props: dict[str, Any] = {}
    organization_id: str

    @field_validator("label")
    @classmethod
    def _label_known(cls, v: str) -> str:
        if v not in NODE_LABELS:
            raise ValueError(f"unknown node label: {v}")
        return v

    @field_validator("id")
    @classmethod
    def _id_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("node id must be non-empty")
        return v

    def sanitized_props(self) -> dict[str, Any]:
        """Drop null props so upserts never overwrite existing values with null."""
        return {k: v for k, v in self.props.items() if v is not None}


class EdgeUpsert(BaseModel):
    """Idempotent directed relationship write with provenance."""

    rel_type: str
    src_label: str
    src_id: str
    dst_label: str
    dst_id: str
    props: dict[str, Any] = {}
    organization_id: str

    @field_validator("rel_type")
    @classmethod
    def _rel_known(cls, v: str) -> str:
        if v not in RELATION_SEMANTICS:
            raise ValueError(f"unknown relationship type: {v}")
        return v

    def validate_endpoints(self) -> Self:
        expected = RELATION_SEMANTICS[self.rel_type]
        if (self.src_label, self.dst_label) != expected:
            raise ValueError(
                f"{self.rel_type} must go {expected[0]} -> {expected[1]}, "
                f"got {self.src_label} -> {self.dst_label}"
            )
        if "provenanceType" not in self.props:
            raise ValueError(f"{self.rel_type} edge requires provenanceType")
        prov = self.props["provenanceType"]
        allowed = ("PROVIDER_EXPLICIT", "MANUAL", "STATIC_ANALYSIS", "RUNTIME_OBSERVED", "INFERRED")
        if prov not in allowed:
            raise ValueError(f"invalid provenanceType: {prov}")
        return self

    def sanitized_props(self) -> dict[str, Any]:
        return {k: v for k, v in self.props.items() if v is not None}


class NodeView(BaseModel):
    label: str
    id: str
    props: dict[str, Any] = {}


class EdgeView(BaseModel):
    rel_type: str
    src: str
    dst: str
    props: dict[str, Any] = {}


class PathView(BaseModel):
    """Ordered traversal: nodes[0] is the start; edges[i] goes nodes[i] -> nodes[i+1]
    (direction preserved from the graph)."""

    nodes: list[NodeView]
    edges: list[EdgeView]


class GraphView(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
