// Graph explorer (FR-07, FR-08). The payload is bounded: depth and limit come
// from the request, the server clamps them to its configured ceiling, and the
// response reports `truncated` when a bound was reached. The table beside the
// graph is the accessible alternative view of exactly the same bounded payload.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import cytoscape, { Core, ElementDefinition, NodeSingular } from "cytoscape";
import { neighborhood, pathsBetween } from "../api/client";
import { NODE_TYPES, REL_TYPES } from "../api/types";
import { EdgeRow, EntityLink } from "../components/entities";
import { EmptyState, ErrorState, Loading, Panel, TruncationNote } from "../components/ui";

const MAX_DEPTH = 10;
const MAX_LIMIT = 200;

function edgeKey(relType: string, src: string, dst: string): string {
  return `${relType}:${src}->${dst}`;
}

export function ExplorerPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  const [entity, setEntity] = useState(params.get("entity") ?? "");
  const [target, setTarget] = useState(params.get("to") ?? "");
  const [depth, setDepth] = useState(3);
  const [limit, setLimit] = useState(50);
  const [nodeTypes, setNodeTypes] = useState<string[]>([]);
  const [relTypes, setRelTypes] = useState<string[]>([]);
  const [showTable, setShowTable] = useState(true);

  const graphQ = useQuery({
    queryKey: ["neighborhood", entity, depth, limit, nodeTypes, relTypes],
    queryFn: () => neighborhood(entity, depth, limit, nodeTypes, relTypes),
    enabled: entity.trim().length > 0,
  });

  const pathsQ = useQuery({
    queryKey: ["paths", entity, target, depth, limit, relTypes],
    queryFn: () => pathsBetween(entity, target, depth, limit, relTypes),
    enabled: entity.trim().length > 0 && target.trim().length > 0,
  });

  const pathKeys = useMemo(() => {
    const keys = new Set<string>();
    const nodeIds = new Set<string>();
    for (const path of pathsQ.data?.paths ?? []) {
      for (const edge of path.relationships) keys.add(edgeKey(edge.relType, edge.srcId, edge.dstId));
      for (const node of path.entities) nodeIds.add(node.entityId);
    }
    return { keys, nodeIds };
  }, [pathsQ.data]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !graphQ.data) return;
    const data = graphQ.data;

    const elements: ElementDefinition[] = [];
    for (const node of data.nodes) {
      elements.push({
        group: "nodes",
        data: {
          id: node.entityId,
          label: node.name,
          type: node.entityType,
          demo: node.isDemoData,
        },
        classes: [
          node.entityType.toLowerCase(),
          ...(node.entityId === data.subject.entityId ? ["subject"] : []),
          ...(node.isDemoData ? ["demo"] : []),
          ...(pathKeys.nodeIds.has(node.entityId) ? ["path"] : []),
        ],
      });
    }
    for (const edge of data.edges) {
      const key = edgeKey(edge.relType, edge.srcId, edge.dstId);
      elements.push({
        group: "edges",
        data: {
          id: key,
          source: edge.srcId,
          target: edge.dstId,
          label: edge.relType,
          provenance: edge.provenanceType ?? "",
        },
        classes: [
          edge.relType.toLowerCase(),
          ...(edge.provenanceType === "MANUAL" ? ["manual"] : []),
          ...(pathKeys.keys.has(key) ? ["path"] : []),
        ],
      });
    }

    const cy = cytoscape({
      container: el,
      elements,
      style: [
        { selector: "node", style: { "font-size": 11, label: "data(label)", "text-wrap": "wrap", width: 26, height: 26 } },
        { selector: "node.subject", style: { "background-color": "#e0a83c", width: 34, height: 34, "border-width": 2, "border-color": "#fff" } },
        { selector: "node.demo", style: { "background-color": "#4f7ea8" } },
        { selector: "node.path", style: { "border-width": 2, "border-color": "#8fd3a8" } },
        { selector: "edge", style: { width: 1.4, "line-color": "#5a6b7d", "target-arrow-shape": "triangle", "curve-style": "bezier", "font-size": 9, label: "data(label)" } },
        { selector: "edge.path", style: { width: 3, "line-color": "#8fd3a8", "target-arrow-color": "#8fd3a8" } },
        { selector: "edge.manual", style: { "line-style": "dashed", "line-color": "#7c8fa6" } },
      ],
      layout: { name: "breadthfirst", spacingFactor: 1.4, fit: true, padding: 20 },
    });
    cyRef.current = cy;
    // Cytoscape types `event.target` as `any` for a generic `on(...)` handler;
    // the node selector guarantees a single node, so `NodeSingular` (which
    // declares `id()`) is the honest alias.
    cy.on("click", "node", (event) => {
      const clicked = event.target as NodeSingular;
      navigate(`/entities/${encodeURIComponent(clicked.id())}`);
    });
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [graphQ.data, pathKeys, navigate]);

  function toggle(list: string[], value: string): string[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  return (
    <div className="explorer">
      <Panel title="Explorer" note="bounded neighborhood + path highlighting">
        <form
          className="explorer-form"
          onSubmit={(event) => {
            event.preventDefault();
            const next = new URLSearchParams(params);
            if (entity.trim()) next.set("entity", entity.trim());
            if (target.trim()) next.set("to", target.trim());
            setParams(next);
          }}
        >
          <label>
            Subject entity id
            <input
              value={entity}
              onChange={(e) => setEntity(e.target.value)}
              placeholder="ado:acme:Commerce:repository:r-uuid-1"
              maxLength={160}
            />
          </label>
          <label>
            Path target (optional)
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="ado:acme:team:t-uuid-1"
              maxLength={160}
            />
          </label>
          <label>
            depth {depth}
            <input
              type="range"
              min={1}
              max={MAX_DEPTH}
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
            />
          </label>
          <label>
            limit {limit}
            <input
              type="range"
              min={10}
              max={MAX_LIMIT}
              step={10}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
          </label>
          <button type="submit" disabled={entity.trim().length === 0}>
            Load
          </button>
        </form>
        <fieldset>
          <legend>node types (none = all)</legend>
          {NODE_TYPES.map((t) => (
            <label key={t} className="chip">
              <input type="checkbox" checked={nodeTypes.includes(t)} onChange={() => setNodeTypes((p) => toggle(p, t))} />
              {t}
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend>relationship types (none = all)</legend>
          {REL_TYPES.map((t) => (
            <label key={t} className="chip">
              <input type="checkbox" checked={relTypes.includes(t)} onChange={() => setRelTypes((p) => toggle(p, t))} />
              {t}
            </label>
          ))}
        </fieldset>
        <p className="note">
          The server clamps depth and limit to its configured ceiling and echoes the values it used.
          Solid edges are imported, dashed edges were configured manually.
        </p>
      </Panel>

      <Panel
        title="Graph"
        note={graphQ.data ? `depth ${graphQ.data.depth} · limit ${graphQ.data.limit} · ${graphQ.data.nodes.length} nodes / ${graphQ.data.edges.length} edges` : "no subject selected"}
        actions={
          <button type="button" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "hide table" : "show table"}
          </button>
        }
      >
        {entity.trim().length === 0 && (
          <p className="state empty">
            Pick an entity to explore.
            <span className="hint">Search first, then use the “explore” link on a result.</span>
          </p>
        )}
        {graphQ.isPending && entity.trim().length > 0 && <Loading label="loading neighborhood…" />}
        {graphQ.isError && <ErrorState error={graphQ.error} />}
        {graphQ.data && (
          <>
            <TruncationNote truncated={graphQ.data.truncated} limit={graphQ.data.limit} depth={graphQ.data.depth} />
            <div ref={containerRef} className="cy" aria-label="engineering graph" />
            {graphQ.data.nodes.length === 0 && <EmptyState title="No nodes inside the bound" />}
          </>
        )}
      </Panel>

      {showTable && graphQ.data && graphQ.data.nodes.length > 0 && (
        <Panel title="Table view" note="same bounded payload">
          <div className="columns">
            <div>
              <h3>Nodes</h3>
              <ul className="compact">
                {graphQ.data.nodes.map((node) => (
                  <li key={node.entityId}>
                    <EntityLink node={node} />
                    <span className="meta">
                      {node.entityType} · {node.sourceSystem ?? "local"}
                      {node.entityId === graphQ.data.subject.entityId ? " · subject" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3>Edges</h3>
              <ul className="edge-list">
                {graphQ.data.edges.map((edge) => (
                  <EdgeRow key={edgeKey(edge.relType, edge.srcId, edge.dstId)} edge={edge} />
                ))}
              </ul>
            </div>
          </div>
        </Panel>
      )}

      {target.trim().length > 0 && (
        <Panel title="Paths" note={`${pathsQ.data?.paths.length ?? 0} path(s) within depth ${depth}`}>
          {pathsQ.isPending && <Loading label="finding paths…" />}
          {pathsQ.isError && <ErrorState error={pathsQ.error} />}
          {pathsQ.data && pathsQ.data.paths.length === 0 && (
            <EmptyState
              title="No path within the bound"
              hint="Raise depth, widen the relationship filter, or check the target id."
            />
          )}
          {pathsQ.data && pathsQ.data.paths.length > 0 && (
            <>
              <TruncationNote truncated={pathsQ.data.truncated} limit={pathsQ.data.limit} depth={pathsQ.data.depth} />
              <ol className="path-list">
                {pathsQ.data.paths.map((path, index) => (
                  <li key={index}>
                    <div className="path-steps">
                      {path.entities.map((node, i) => (
                        <span key={node.entityId} className="step">
                          <EntityLink node={node} />
                          {i < path.relationships.length && (
                            <span className="arrow">{path.relationships[i].relType}</span>
                          )}
                        </span>
                      ))}
                    </div>
                    <span className="meta">
                      {path.relationships.length} edge(s) ·{" "}
                      {path.relationships.map((e) => e.provenanceType ?? "?").join(", ")}
                    </span>
                  </li>
                ))}
              </ol>
            </>
          )}
          <Link className="link-button" to={`/impact?subject=${encodeURIComponent(entity.trim())}`}>
            Run change-impact analysis on this subject
          </Link>
        </Panel>
      )}
    </div>
  );
}
