"""Neo4j graph repository (production store).

Official neo4j Python driver. Every query is parameterized; labels and
relationship types are inserted from internal allowlists only (never user
input). Traversals are bounded by depth and limit. Sessions are created per
transaction via execute_query — never shared across concurrent requests.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from neo4j import Driver

from enggraph.graph.model import (
    MANUAL_SOURCE_SYSTEM,
    NODE_LABELS,
    RELATION_SEMANTICS,
    EdgeUpsert,
    EdgeView,
    GraphView,
    NodeUpsert,
    NodeView,
    PathView,
)
from enggraph.graph.repository import DEFAULT_SEARCH_FIELDS

log = logging.getLogger("enggraph.graph.neo4j")

# Labels/types must be simple identifiers; enforced before query construction.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z_]*$")


def _safe_label(label: str) -> str:
    if label not in NODE_LABELS or not _IDENTIFIER_RE.match(label):
        raise ValueError(f"label not in allowlist: {label}")
    return label


def _safe_rel(rel_type: str) -> str:
    if rel_type not in RELATION_SEMANTICS or not _IDENTIFIER_RE.match(rel_type):
        raise ValueError(f"relationship type not in allowlist: {rel_type}")
    return rel_type


def _record_to_node(record: dict[str, Any]) -> NodeView:
    return NodeView(label=record["label"], id=record["id"], props=dict(record["props"]))


class Neo4jGraphRepository:
    def __init__(self, driver: Driver, database: str = "neo4j") -> None:
        self.driver = driver
        self.database = database

    def _run(self, cypher: str, **params: Any) -> list[Any]:
        result = self.driver.execute_query(cypher, parameters_=params, database_=self.database)
        return list(result.records)

    # ------------------------------------------------------------------ writes

    def upsert_nodes(self, nodes: list[NodeUpsert]) -> int:
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list[Any])
        for n in nodes:
            label = _safe_label(n.label)
            by_label[label].append(
                {"id": n.id, "props": n.sanitized_props(), "organizationId": n.organization_id}
            )
        total = 0
        for label, rows in by_label.items():
            cypher = (
                "UNWIND $rows AS row "
                f"MERGE (n:`{label}` {{id: row.id}}) "
                "ON CREATE SET n += row.props, n.organizationId = row.organizationId "
                "ON MATCH SET n += row.props, n.organizationId = row.organizationId"
            )
            self._run(cypher, rows=rows)
            total += len(rows)
        return total

    def upsert_edges(self, edges: list[EdgeUpsert]) -> int:
        by_triple: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list[Any])
        for e in edges:
            e.validate_endpoints()
            src_label = _safe_label(e.src_label)
            dst_label = _safe_label(e.dst_label)
            rel = _safe_rel(e.rel_type)
            by_triple[(src_label, rel, dst_label)].append(
                {"src": e.src_id, "dst": e.dst_id, "props": e.sanitized_props()}
            )
        total = 0
        for (src_label, rel, dst_label), rows in by_triple.items():
            # MATCH (not MERGE on nodes): never create placeholder nodes.
            cypher = (
                "UNWIND $rows AS row "
                f"MATCH (a:`{src_label}` {{id: row.src}}), (b:`{dst_label}` {{id: row.dst}}) "
                f"MERGE (a)-[r:`{rel}`]->(b) "
                "SET r += row.props "
                "RETURN count(*) AS matched"
            )
            records = self._run(cypher, rows=rows)
            total += int(records[0]["matched"]) if records else 0
        return total

    # ------------------------------------------------------------------ reads

    def get_node(self, node_id: str, org_id: str) -> NodeView | None:
        records = self._run(
            "MATCH (n {id: $id}) WHERE n.organizationId = $org "
            "RETURN labels(n)[0] AS label, n.id AS id, properties(n) AS props",
            id=node_id,
            org=org_id,
        )
        return _record_to_node(records[0]) if records else None

    def _search_where(self) -> str:
        fields = " OR ".join(
            f"toLower(coalesce(n.`{f}`, '')) CONTAINS $q" for f in DEFAULT_SEARCH_FIELDS
        )
        return f"({fields})"

    def search(
        self, q: str, labels: list[str], org_id: str, limit: int, offset: int = 0
    ) -> list[NodeView]:
        safe_labels = [_safe_label(s) for s in labels]
        label_filter = " OR ".join(f"n:`{s}`" for s in safe_labels)
        records = self._run(
            "MATCH (n) WHERE n.organizationId = $org AND NOT coalesce(n.isDeleted, false) "
            f"AND ({label_filter}) AND {self._search_where()} "
            "RETURN labels(n)[0] AS label, n.id AS id, properties(n) AS props "
            "ORDER BY label, id SKIP $offset LIMIT $limit",
            q=q.lower(),
            org=org_id,
            offset=offset,
            limit=limit,
        )
        return [_record_to_node(r) for r in records]

    def search_count(self, q: str, labels: list[str], org_id: str) -> int:
        safe_labels = [_safe_label(s) for s in labels]
        label_filter = " OR ".join(f"n:`{s}`" for s in safe_labels)
        records = self._run(
            "MATCH (n) WHERE n.organizationId = $org AND NOT coalesce(n.isDeleted, false) "
            f"AND ({label_filter}) AND {self._search_where()} "
            "RETURN count(n) AS c",
            q=q.lower(),
            org=org_id,
        )
        return int(records[0]["c"]) if records else 0

    def outgoing(
        self, node_id: str, org_id: str, rel_types: list[str], limit: int
    ) -> list[EdgeView]:
        safe_rels = [_safe_rel(r) for r in rel_types] if rel_types else list(RELATION_SEMANTICS)
        rel_filter = " OR ".join(f"type(r) = `{r}`" for r in safe_rels)
        records = self._run(
            "MATCH (a {id: $id})-[r]->(b) "
            "WHERE a.organizationId = $org AND b.organizationId = $org "
            f"AND ({rel_filter}) "
            "RETURN type(r) AS rel, a.id AS src, b.id AS dst, properties(r) AS props "
            "ORDER BY rel, dst LIMIT $limit",
            id=node_id,
            org=org_id,
            limit=limit,
        )
        return [
            EdgeView(rel_type=r["rel"], src=r["src"], dst=r["dst"], props=dict(r["props"]))
            for r in records
        ]

    def incoming(
        self, node_id: str, org_id: str, rel_types: list[str], limit: int
    ) -> list[EdgeView]:
        safe_rels = [_safe_rel(r) for r in rel_types] if rel_types else list(RELATION_SEMANTICS)
        rel_filter = " OR ".join(f"type(r) = `{r}`" for r in safe_rels)
        records = self._run(
            "MATCH (a)-[r]->(b {id: $id}) "
            "WHERE a.organizationId = $org AND b.organizationId = $org "
            f"AND ({rel_filter}) "
            "RETURN type(r) AS rel, a.id AS src, b.id AS dst, properties(r) AS props "
            "ORDER BY rel, src LIMIT $limit",
            id=node_id,
            org=org_id,
            limit=limit,
        )
        return [
            EdgeView(rel_type=r["rel"], src=r["src"], dst=r["dst"], props=dict(r["props"]))
            for r in records
        ]

    def neighborhood(
        self,
        node_id: str,
        org_id: str,
        node_types: list[str],
        rel_types: list[str],
        depth: int,
        limit: int,
    ) -> GraphView:
        start = self.get_node(node_id, org_id)
        if start is None:
            return GraphView(nodes=[], edges=[])
        safe_node_types = [_safe_label(t) for t in node_types] if node_types else list(NODE_LABELS)
        safe_rels = [_safe_rel(r) for r in rel_types] if rel_types else list(RELATION_SEMANTICS)
        node_filter = " OR ".join(f"labels(b)[0] = `{t}`" for t in safe_node_types)
        rel_filter = " OR ".join(f"type(r) = `{r}`" for r in safe_rels)
        visited: dict[str, NodeView] = {start.id: start}
        edges: dict[tuple[str, str, str], EdgeView] = {}
        frontier = [node_id]
        for _ in range(max(depth, 0)):
            if not frontier or len(visited) >= limit:
                break
            records = self._run(
                "UNWIND $frontier AS nid "
                "MATCH (a {id: nid})-[r]-(b) "
                "WHERE a.organizationId = $org AND b.organizationId = $org "
                f"AND ({rel_filter}) AND ({node_filter}) "
                "RETURN b.id AS id, labels(b)[0] AS label, properties(b) AS props, "
                "type(r) AS rel, a.id AS src, b.id AS dst, properties(r) AS props "
                "LIMIT $fanout",
                frontier=frontier,
                org=org_id,
                fanout=min(limit - len(visited), limit),
            )
            next_frontier: list[str] = []
            for r in records:
                edge = EdgeView(
                    rel_type=r["rel"], src=r["src"], dst=r["dst"], props=dict(r["props"])
                )
                key = (edge.src, edge.rel_type, edge.dst)
                if key not in edges:
                    edges[key] = edge
                if r["id"] not in visited and len(visited) < limit:
                    visited[r["id"]] = _record_to_node(r)
                    next_frontier.append(r["id"])
            frontier = next_frontier
        kept_edges = [e for e in edges.values() if e.src in visited and e.dst in visited]
        return GraphView(nodes=list(visited.values()), edges=kept_edges)

    def find_paths(
        self,
        from_id: str,
        to_id: str,
        org_id: str,
        rel_types: list[str],
        max_depth: int,
        limit: int,
    ) -> list[PathView]:
        safe_rels = [_safe_rel(r) for r in rel_types]
        rel_pattern = "|".join(f"`{r}`" for r in safe_rels)
        # max_depth is validated as int (1..10) by callers; interpolated as int only.
        depth = int(max_depth)
        # "from" is a Python keyword, so params are passed via dict.
        records = self._run(
            "MATCH p = (a {id: $from, organizationId: $org})-"
            f"[r:{rel_pattern}*1..{depth}]-"
            "(b {id: $to, organizationId: $org}) "
            "RETURN p LIMIT $limit",
            **{"from": from_id, "to": to_id, "org": org_id, "limit": limit},
        )
        paths: list[PathView] = []
        for record in records:
            path = record["p"]
            nodes = [
                NodeView(label=list(n.labels)[0], id=n["id"], props=dict(n)) for n in path.nodes
            ]
            edges = [
                EdgeView(
                    rel_type=e.type,
                    src=e.start["id"],
                    dst=e.end["id"],
                    props=dict(e),
                )
                for e in path.edges
            ]
            paths.append(PathView(nodes=nodes, edges=edges))
        return paths

    def expand_paths(
        self, node_id: str, org_id: str, rel_types: list[str], depth: int, limit: int
    ) -> list[PathView]:
        """Bounded expansion from one node, keeping one explaining path per node.

        Undirected (impact analysis reads both directions), org-scoped, rel
        allowlisted, depth-bounded, limit-bounded. Parent links form a spanning
        tree so each reported path is a shortest-first witness.
        """
        start = self.get_node(node_id, org_id)
        if start is None:
            return []
        safe_rels = [_safe_rel(r) for r in rel_types] if rel_types else list(RELATION_SEMANTICS)
        rel_pattern = "|".join(f"`{r}`" for r in safe_rels)
        visited: dict[str, NodeView] = {node_id: start}
        parent: dict[str, tuple[str, EdgeView]] = {}
        frontier = [node_id]
        for _ in range(max(depth, 0)):
            if not frontier or len(visited) >= limit:
                break
            records = self._run(
                "UNWIND $frontier AS nid "
                f"MATCH (a {{id: nid}})-[r:`{rel_pattern}`]-(b) "
                "WHERE a.organizationId = $org AND b.organizationId = $org "
                "RETURN b.id AS id, a.id AS parent, type(r) AS rel, "
                "startNode(r).id AS edge_src, endNode(r).id AS edge_dst, "
                "properties(r) AS props, labels(b)[0] AS label, properties(b) AS node_props "
                "LIMIT $fanout",
                frontier=frontier,
                org=org_id,
                fanout=min(limit - len(visited), limit),
            )
            next_frontier: list[str] = []
            for r in records:
                if r["id"] in visited:
                    continue
                edge = EdgeView(
                    rel_type=r["rel"], src=r["edge_src"], dst=r["edge_dst"], props=dict(r["props"])
                )
                visited[r["id"]] = NodeView(
                    label=r["label"], id=r["id"], props=dict(r["node_props"])
                )
                parent[r["id"]] = (r["parent"], edge)
                next_frontier.append(r["id"])
                if len(visited) >= limit:
                    break
            frontier = next_frontier
        paths: list[PathView] = []
        for node in sorted(visited):
            if node == node_id:
                continue
            chain: list[EdgeView] = []
            cursor = node
            while cursor != node_id:
                prev, edge = parent[cursor]
                chain.append(edge)
                cursor = prev
            chain.reverse()
            vertices = [node_id]
            for edge in chain:
                vertices.append(edge.dst if edge.src == vertices[-1] else edge.src)
            paths.append(PathView(nodes=[visited[v] for v in vertices], edges=chain))
        return paths[:limit]

    def list_by_label(self, label: str, org_id: str, limit: int, offset: int = 0) -> list[NodeView]:
        safe = _safe_label(label)
        records = self._run(
            f"MATCH (n:`{safe}`) WHERE n.organizationId = $org "
            "AND NOT coalesce(n.isDeleted, false) "
            "RETURN labels(n)[0] AS label, n.id AS id, properties(n) AS props "
            "ORDER BY n.id SKIP $offset LIMIT $limit",
            org=org_id,
            offset=offset,
            limit=limit,
        )
        return [_record_to_node(r) for r in records]

    def count_by_label(self, label: str, org_id: str) -> int:
        safe = _safe_label(label)
        records = self._run(
            f"MATCH (n:`{safe}`) WHERE n.organizationId = $org "
            "AND NOT coalesce(n.isDeleted, false) RETURN count(n) AS c",
            org=org_id,
        )
        return int(records[0]["c"]) if records else 0

    def delete_edge(self, rel_type: str, src_id: str, dst_id: str, org_id: str) -> int:
        rel = _safe_rel(rel_type)
        records = self._run(
            f"MATCH (a {{id: $src}})-[r:`{rel}`]->(b {{id: $dst}}) "
            "WHERE a.organizationId = $org AND b.organizationId = $org "
            "DELETE r RETURN count(r) AS c",
            src=src_id,
            dst=dst_id,
            org=org_id,
        )
        return int(records[0]["c"]) if records else 0

    def mark_not_seen(self, org_id: str, cutoff_iso: str, labels: list[str]) -> int:
        """Reconcile provider-unknown nodes; manual catalog and demo nodes are excluded.

        `n.sourceSystem IS NULL` stays eligible so a provider node written before the
        property existed is still reconciled. Parameters only — the label filter comes
        from the internal allowlist.
        """
        safe_labels = [_safe_label(s) for s in labels] if labels else list(NODE_LABELS)
        label_filter = " OR ".join(f"n:`{s}`" for s in safe_labels)
        records = self._run(
            "MATCH (n) WHERE n.organizationId = $org AND NOT coalesce(n.isDeleted, false) "
            f"AND ({label_filter}) AND n.lastSeenAt IS NOT NULL AND n.lastSeenAt < $cutoff "
            "AND coalesce(n.demoData, false) = false "
            "AND (n.sourceSystem IS NULL OR n.sourceSystem <> $manual_source) "
            "SET n.isDeleted = true, n.deletedAt = $now RETURN count(n) AS c",
            org=org_id,
            cutoff=cutoff_iso,
            now=cutoff_iso,
            manual_source=MANUAL_SOURCE_SYSTEM,
        )
        return int(records[0]["c"]) if records else 0

    def delete_demo(self, org_id: str) -> int:
        records = self._run(
            "MATCH (n) WHERE n.organizationId = $org AND n.demoData = true "
            "DETACH DELETE n RETURN count(n) AS c",
            org=org_id,
        )
        return int(records[0]["c"]) if records else 0

    def counts(self, org_id: str) -> dict[str, int]:
        records = self._run(
            "MATCH (n) WHERE n.organizationId = $org AND NOT coalesce(n.isDeleted, false) "
            "RETURN labels(n)[0] AS label, count(n) AS c ORDER BY label",
            org=org_id,
        )
        return {r["label"]: int(r["c"]) for r in records}
