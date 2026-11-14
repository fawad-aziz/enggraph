"""Graph repository interface.

Domain services depend only on this protocol. Two implementations exist:

- Neo4jGraphRepository: production store (official driver, parameterized,
  bounded queries).
- InMemoryGraphRepository: test-only engine with identical semantics, used
  so unit/E2E tests run without a Neo4j container. Production code paths
  never use it.
"""

from __future__ import annotations

from typing import Protocol

from enggraph.graph.model import EdgeUpsert, EdgeView, GraphView, NodeUpsert, NodeView, PathView

DEFAULT_SEARCH_FIELDS = ("name", "title", "displayName")


class GraphRepository(Protocol):
    def upsert_nodes(self, nodes: list[NodeUpsert]) -> int: ...

    def upsert_edges(self, edges: list[EdgeUpsert]) -> int: ...

    def get_node(self, node_id: str, org_id: str) -> NodeView | None: ...

    def search(
        self, q: str, labels: list[str], org_id: str, limit: int, offset: int = 0
    ) -> list[NodeView]: ...

    def search_count(self, q: str, labels: list[str], org_id: str) -> int: ...

    def outgoing(
        self, node_id: str, org_id: str, rel_types: list[str], limit: int
    ) -> list[EdgeView]: ...

    def incoming(
        self, node_id: str, org_id: str, rel_types: list[str], limit: int
    ) -> list[EdgeView]: ...

    def neighborhood(
        self,
        node_id: str,
        org_id: str,
        node_types: list[str],
        rel_types: list[str],
        depth: int,
        limit: int,
    ) -> GraphView: ...

    def find_paths(
        self,
        from_id: str,
        to_id: str,
        org_id: str,
        rel_types: list[str],
        max_depth: int,
        limit: int,
    ) -> list[PathView]: ...

    def mark_not_seen(self, org_id: str, cutoff_iso: str, labels: list[str]) -> int: ...

    def delete_demo(self, org_id: str) -> int: ...

    def counts(self, org_id: str) -> dict[str, int]: ...
