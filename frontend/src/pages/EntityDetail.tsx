import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { entityRelationships, getEntity } from "../api/client";
import type { RelationshipsResponse } from "../api/types";
import { EdgeRow, PropList, SourceLink } from "../components/entities";
import { EmptyState, ErrorState, fmtTime, Loading, Panel, ProvenanceTag, TruncationNote } from "../components/ui";

const REL_LIMIT = 50;

export function EntityDetailPage() {
  const entityId = useParams().entityId ?? "";
  const [relLimit, setRelLimit] = useState(REL_LIMIT);

  const nodeQ = useQuery({
    queryKey: ["entity", entityId],
    queryFn: () => getEntity(entityId),
    enabled: entityId.length > 0,
  });
  const relQ = useQuery({
    queryKey: ["relationships", entityId, relLimit],
    queryFn: () => entityRelationships(entityId, relLimit),
    enabled: entityId.length > 0,
  });

  const groups: { relType: string; edges: RelationshipsResponse["items"] }[] = [];
  if (relQ.data) {
    const byType = new Map<string, RelationshipsResponse["items"]>();
    for (const item of relQ.data.items) {
      const bucket = byType.get(item.relType) ?? [];
      bucket.push(item);
      byType.set(item.relType, bucket);
    }
    for (const [relType, edges] of byType) groups.push({ relType, edges });
    groups.sort((a, b) => a.relType.localeCompare(b.relType));
  }

  return (
    <div className="grid">
      <Panel
        title={nodeQ.data ? nodeQ.data.name : "Entity"}
        note={nodeQ.data ? nodeQ.data.entityType : undefined}
        actions={
          nodeQ.data && (
            <span className="panel-actions">
              <Link to={`/explorer?entity=${encodeURIComponent(entityId)}`}>explore</Link>
              <Link to={`/impact?subject=${encodeURIComponent(entityId)}`}>impact</Link>
            </span>
          )
        }
      >
        {entityId.length === 0 && <p className="state empty">No entity id in the route.</p>}
        {nodeQ.isPending && entityId.length > 0 && <Loading label="loading entity…" />}
        {nodeQ.isError && <ErrorState error={nodeQ.error} />}
        {nodeQ.data && (
          <>
            <dl className="kv">
              <div>
                <dt>canonical id</dt>
                <dd>{nodeQ.data.entityId}</dd>
              </div>
              <div>
                <dt>source system</dt>
                <dd>{nodeQ.data.sourceSystem ?? "—"}</dd>
              </div>
              <div>
                <dt>source id</dt>
                <dd>{nodeQ.data.sourceId ?? "—"}</dd>
              </div>
              <div>
                <dt>last seen</dt>
                <dd>{fmtTime(nodeQ.data.lastSeenAt)}</dd>
              </div>
            </dl>
            <div className="card-actions">
              <SourceLink node={nodeQ.data} />
              {nodeQ.data.isDemoData && (
                <span className="tag demo">
                  demo data — not imported from a live provider
                </span>
              )}
            </div>
            <h3>Properties</h3>
            <PropList properties={nodeQ.data.properties} />
          </>
        )}
      </Panel>

      <Panel
        title="Relationships"
        note={relQ.data ? `${relQ.data.items.length} of ${relQ.data.total} · ${relQ.data.limit} fetched` : "direct edges"}
        actions={
          <button
            type="button"
            onClick={() => setRelLimit((n) => Math.min(n * 2, 200))}
            disabled={relLimit >= 200}
            title="asks the server for more edges; the server clamps to its configured ceiling"
          >
            raise fetch limit
          </button>
        }
      >
        {relQ.isPending && entityId.length > 0 && <Loading label="loading relationships…" />}
        {relQ.isError && <ErrorState error={relQ.error} />}
        {relQ.data && relQ.data.items.length === 0 && (
          <EmptyState
            title="No relationships recorded"
            hint="Run a sync, or add a manual link in the Catalog (admin)."
          />
        )}
        {relQ.data && relQ.data.items.length > 0 && (
          <>
            <TruncationNote truncated={relQ.data.truncated} limit={relQ.data.limit} />
            {groups.map(({ relType, edges }) => (
              <div key={relType} className="rel-group">
                <h3 className="reltype">{relType}</h3>
                <ul className="edge-list">
                  {edges.map((edge) => (
                    <EdgeRow
                      key={`${edge.relType}:${edge.srcId}:${edge.dstId}`}
                      edge={edge}
                      direction={edge.direction}
                    />
                  ))}
                </ul>
              </div>
            ))}
            <p className="meta">
              <ProvenanceTag provenance="PROVIDER_EXPLICIT" /> edges imported by sync cannot be edited
              here; they belong to the provider.
            </p>
          </>
        )}
      </Panel>
    </div>
  );
}
