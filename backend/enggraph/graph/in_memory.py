"""In-memory graph engine implementing the GraphRepository interface.

TEST-ONLY. Production runs Neo4jGraphRepository. This engine implements the
same semantics (idempotent upserts, org scoping, bounded traversal,
provenance merge, direction validation) so unit/E2E tests can run without a
Neo4j container. It is never wired into production app context.
"""

from __future__ import annotations

from enggraph.graph.model import EdgeUpsert, EdgeView, GraphView, NodeUpsert, NodeView, PathView
from enggraph.graph.repository import DEFAULT_SEARCH_FIELDS


class InMemoryGraphRepository:
    def __init__(self) -> None:
        self._nodes: dict[str, NodeView] = {}
        # keyed by (src_id, rel_type, dst_id) — unique directed edge, mirrors MERGE
        self._edges: dict[tuple[str, str, str], EdgeView] = {}

    def upsert_nodes(self, nodes: list[NodeUpsert]) -> int:
        count = 0
        for n in nodes:
            existing = self._nodes.get(n.id)
            props = dict(existing.props) if existing else {}
            props.update(n.sanitized_props())
            self._nodes[n.id] = NodeView(label=n.label, id=n.id, props=props)
            count += 1
        return count

    def upsert_edges(self, edges: list[EdgeUpsert]) -> int:
        count = 0
        for e in edges:
            e.validate_endpoints()
            # Reference resolution: never create an edge touching a missing node.
            if e.src_id not in self._nodes or e.dst_id not in self._nodes:
                continue
            key = (e.src_id, e.rel_type, e.dst_id)
            props = dict(self._edges[key].props) if key in self._edges else {}
            props.update(e.sanitized_props())
            self._edges[key] = EdgeView(
                rel_type=e.rel_type, src=e.src_id, dst=e.dst_id, props=props
            )
            count += 1
        return count

    def get_node(self, node_id: str, org_id: str) -> NodeView | None:
        n = self._nodes.get(node_id)
        if n is not None and n.props.get("organizationId") == org_id:
            return n
        return None

    def _matches(self, node: NodeView, q: str) -> bool:
        if not q:
            return True
        q_low = q.lower()
        for field in DEFAULT_SEARCH_FIELDS:
            value = node.props.get(field)
            if value and q_low in str(value).lower():
                return True
        return False

    def _search_hits(self, q: str, labels: list[str], org_id: str) -> list[NodeView]:
        hits = [
            n
            for n in self._nodes.values()
            if n.props.get("organizationId") == org_id
            and n.label in labels
            and not n.props.get("isDeleted")
            and self._matches(n, q)
        ]
        hits.sort(key=lambda n: (n.label, n.id))
        return hits

    def search(
        self, q: str, labels: list[str], org_id: str, limit: int, offset: int = 0
    ) -> list[NodeView]:
        return self._search_hits(q, labels, org_id)[offset : offset + limit]

    def search_count(self, q: str, labels: list[str], org_id: str) -> int:
        return len(self._search_hits(q, labels, org_id))

    def outgoing(
        self, node_id: str, org_id: str, rel_types: list[str], limit: int
    ) -> list[EdgeView]:
        if self.get_node(node_id, org_id) is None:
            return []
        edges = [
            e
            for e in self._edges.values()
            if e.src == node_id
            and (not rel_types or e.rel_type in rel_types)
            and self._nodes[e.dst].props.get("organizationId") == org_id
        ]
        edges.sort(key=lambda e: (e.rel_type, e.dst))
        return edges[:limit]

    def incoming(
        self, node_id: str, org_id: str, rel_types: list[str], limit: int
    ) -> list[EdgeView]:
        if self.get_node(node_id, org_id) is None:
            return []
        edges = [
            e
            for e in self._edges.values()
            if e.dst == node_id
            and (not rel_types or e.rel_type in rel_types)
            and self._nodes[e.src].props.get("organizationId") == org_id
        ]
        edges.sort(key=lambda e: (e.rel_type, e.src))
        return edges[:limit]

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
        visited = {node_id}
        nodes = [start]
        traversed: list[EdgeView] = []
        frontier = {node_id}
        for _ in range(max(depth, 0)):
            if not frontier or len(nodes) >= limit:
                break
            next_frontier: set[str] = set()
            for e in sorted(self._edges.values(), key=lambda e: (e.rel_type, e.src, e.dst)):
                if len(nodes) >= limit:
                    break
                if e.src not in frontier and e.dst not in frontier:
                    continue
                if rel_types and e.rel_type not in rel_types:
                    continue
                other = e.dst if e.src in frontier else e.src
                n = self._nodes[other]
                if n.props.get("organizationId") != org_id:
                    continue
                if node_types and n.label not in node_types:
                    continue
                traversed.append(e)
                if other in visited:
                    continue
                visited.add(other)
                nodes.append(n)
                next_frontier.add(other)
            frontier = next_frontier
        edges = [e for e in traversed if e.src in visited and e.dst in visited]
        return GraphView(nodes=nodes, edges=edges)

    def find_paths(
        self,
        from_id: str,
        to_id: str,
        org_id: str,
        rel_types: list[str],
        max_depth: int,
        limit: int,
    ) -> list[PathView]:
        if self.get_node(from_id, org_id) is None or self.get_node(to_id, org_id) is None:
            return []
        adjacency: dict[str, list[EdgeView]] = {}
        for e in self._edges.values():
            src = self._nodes[e.src].props.get("organizationId")
            dst = self._nodes[e.dst].props.get("organizationId")
            if src != org_id or dst != org_id:
                continue
            if rel_types and e.rel_type not in rel_types:
                continue
            adjacency.setdefault(e.src, []).append(e)
            adjacency.setdefault(e.dst, []).append(e)
        paths: list[PathView] = []

        def dfs(node: str, visited: set[str], path_edges: list[EdgeView]) -> None:
            if len(paths) >= limit:
                return
            if node == to_id and path_edges:
                vertices = [node]
                for e in reversed(path_edges):
                    nxt = e.src if e.dst == vertices[-1] else e.dst
                    vertices.append(nxt)
                vertices.reverse()
                nodes = [self._nodes[v] for v in vertices]
                paths.append(PathView(nodes=nodes, edges=list(path_edges)))
                return
            if len(path_edges) >= max_depth:
                return
            for e in adjacency.get(node, []):
                other = e.dst if e.src == node else e.src
                if other in visited:
                    continue
                dfs(other, visited | {other}, path_edges + [e])

        dfs(from_id, {from_id}, [])
        return paths

    def expand_paths(
        self, node_id: str, org_id: str, rel_types: list[str], depth: int, limit: int
    ) -> list[PathView]:
        """Bounded expansion from one node: one explaining path per reached node.

        Traversal is undirected (impact analysis cares about both directions) and
        stays inside the organization, the relationship allowlist, `depth`, and
        `limit`.
        """
        if self.get_node(node_id, org_id) is None:
            return []
        adjacency: dict[str, list[EdgeView]] = {}
        for e in self._edges.values():
            if rel_types and e.rel_type not in rel_types:
                continue
            if (
                self._nodes[e.src].props.get("organizationId") != org_id
                or self._nodes[e.dst].props.get("organizationId") != org_id
            ):
                continue
            adjacency.setdefault(e.src, []).append(e)
            adjacency.setdefault(e.dst, []).append(e)
        visited = {node_id}
        parent: dict[str, tuple[str, EdgeView]] = {}
        frontier = [node_id]
        for _ in range(max(depth, 0)):
            if not frontier or len(visited) >= limit:
                break
            next_frontier: list[str] = []
            for node in frontier:
                for e in sorted(adjacency.get(node, []), key=lambda e: (e.rel_type, e.src, e.dst)):
                    other = e.dst if e.src == node else e.src
                    if other in visited:
                        continue
                    visited.add(other)
                    parent[other] = (node, e)
                    next_frontier.append(other)
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
            paths.append(PathView(nodes=[self._nodes[v] for v in vertices], edges=chain))
        return paths[:limit]

    def list_by_label(self, label: str, org_id: str, limit: int, offset: int = 0) -> list[NodeView]:
        hits = [
            n
            for n in self._nodes.values()
            if n.label == label
            and n.props.get("organizationId") == org_id
            and not n.props.get("isDeleted")
        ]
        hits.sort(key=lambda n: n.id)
        return hits[offset : offset + limit]

    def count_by_label(self, label: str, org_id: str) -> int:
        return sum(
            1
            for n in self._nodes.values()
            if n.label == label
            and n.props.get("organizationId") == org_id
            and not n.props.get("isDeleted")
        )

    def delete_edge(self, rel_type: str, src_id: str, dst_id: str, org_id: str) -> int:
        key = (src_id, rel_type, dst_id)
        edge = self._edges.get(key)
        if edge is None:
            return 0
        src = self._nodes.get(src_id)
        dst = self._nodes.get(dst_id)
        if src is None or dst is None:
            return 0
        if src.props.get("organizationId") != org_id or dst.props.get("organizationId") != org_id:
            return 0
        del self._edges[key]
        return 1

    def mark_not_seen(self, org_id: str, cutoff_iso: str, labels: list[str]) -> int:
        count = 0
        for n in self._nodes.values():
            if n.props.get("organizationId") != org_id:
                continue
            if labels and n.label not in labels:
                continue
            seen = n.props.get("lastSeenAt")
            if seen is not None and str(seen) < cutoff_iso:
                n.props["isDeleted"] = True
                count += 1
        return count

    def delete_demo(self, org_id: str) -> int:
        doomed = {
            i
            for i, n in self._nodes.items()
            if n.props.get("demoData") is True and n.props.get("organizationId") == org_id
        }
        for key in [k for k, e in self._edges.items() if e.src in doomed or e.dst in doomed]:
            del self._edges[key]
        for node_id in doomed:
            del self._nodes[node_id]
        return len(doomed)

    def counts(self, org_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for n in self._nodes.values():
            if n.props.get("organizationId") == org_id and not n.props.get("isDeleted"):
                result[n.label] = result.get(n.label, 0) + 1
        return result
