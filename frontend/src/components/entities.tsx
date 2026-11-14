// Entity rendering shared by search, explorer, catalog and impact screens.
// Every node links to its detail page, and provider nodes link to the source
// record so a result can always be traced to evidence.

import { Link } from "react-router-dom";
import type { EdgeDto, NodeDto } from "../api/types";
import { fmtTime, ProvenanceTag } from "./ui";

const DIRECTION_LABEL = { outgoing: "out", incoming: "in" } as const;

export function EntityLink({ node }: { node: { entityId: string; name: string; entityType: string } }) {
  return (
    <Link to={`/entities/${encodeURIComponent(node.entityId)}`} className="entity-link">
      <span className="etype">{node.entityType}</span>
      <span className="ename">{node.name}</span>
    </Link>
  );
}

export function SourceLink({ node }: { node: NodeDto }) {
  if (!node.sourceUrl) {
    return (
      <span className="source local">
        EngGraph-managed{node.sourceSystem ? ` (${node.sourceSystem})` : ""}
      </span>
    );
  }
  return (
    <a
      className="source"
      href={node.sourceUrl}
      target="_blank"
      rel="noreferrer noopener"
    >
      Open in {node.sourceSystem ?? "source"} · {node.sourceId ?? node.entityId}
    </a>
  );
}

export function PropList({ properties }: { properties: Record<string, unknown> }) {
  const entries = Object.entries(properties);
  if (entries.length === 0) return <p className="state empty">No properties recorded.</p>;
  return (
    <dl className="props">
      {entries.map(([key, value]) => (
        <div key={key} className="prop">
          <dt>{key}</dt>
          <dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function NodeCard({ node }: { node: NodeDto }) {
  return (
    <article className={`node-card${node.isDemoData ? " demo" : ""}`}>
      <header>
        <span className="etype">{node.entityType}</span>
        <span className="ename">{node.name}</span>
        {node.isDemoData && <span className="tag demo">demo data</span>}
      </header>
      <dl className="kv">
        <div>
          <dt>canonical id</dt>
          <dd>{node.entityId}</dd>
        </div>
        <div>
          <dt>source system</dt>
          <dd>{node.sourceSystem ?? "—"}</dd>
        </div>
        <div>
          <dt>source id</dt>
          <dd>{node.sourceId ?? "—"}</dd>
        </div>
        <div>
          <dt>last seen</dt>
          <dd>{fmtTime(node.lastSeenAt)}</dd>
        </div>
      </dl>
      <div className="card-actions">
        <EntityLink node={node} />
        <SourceLink node={node} />
      </div>
    </article>
  );
}

export function EdgeRow({ edge, direction }: { edge: EdgeDto; direction?: "outgoing" | "incoming" }) {
  return (
    <li className="edge-row">
      <span className="reltype">
        {direction ? DIRECTION_LABEL[direction] : "dir"}: {edge.relType}
      </span>
      <span className="edge-endpoints">
        <EntityLink node={{ entityId: edge.srcId, name: edge.srcId, entityType: "entity" }} />
        <span className="arrow">→</span>
        <EntityLink node={{ entityId: edge.dstId, name: edge.dstId, entityType: "entity" }} />
      </span>
      <ProvenanceTag provenance={edge.provenanceType} />
      <span className="meta">
        {edge.sourceSystem ? `source: ${edge.sourceSystem}` : "no source system"}
        {edge.sourceRecordId ? ` · ${edge.sourceRecordId}` : ""}
        {edge.createdBy ? ` · by ${edge.createdBy}` : ""}
        {edge.observedAt ? ` · ${fmtTime(edge.observedAt)}` : ""}
      </span>
    </li>
  );
}
