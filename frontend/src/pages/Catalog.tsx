// Engineering catalog (FR-06): services, APIs and the manually configured
// ownership / dependency links. Reads are available to every member of the
// organization; writes are admin-only and the server enforces that — this page
// only reflects the role, and a non-admin sees the controls absent.

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  createApi,
  createRelationship,
  createService,
  deleteRelationship,
  listApis,
  listManualRelationships,
  listServices,
  updateService,
} from "../api/client";
import { MANUAL_REL_TYPES, type EdgeDto, type NodeDto } from "../api/types";
import { EdgeRow, EntityLink } from "../components/entities";
import { EmptyState, ErrorState, Loading, Panel, Pagination, ProvenanceTag } from "../components/ui";
import { useIsAdmin } from "../session";

const PAGE = 20;

export function CatalogPage() {
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [name, setName] = useState("");
  const [tier, setTier] = useState("");
  const [description, setDescription] = useState("");
  const [apiName, setApiName] = useState("");
  const [apiMethod, setApiMethod] = useState("GET");
  const [apiPath, setApiPath] = useState("");
  const [relType, setRelType] = useState<string>("OWNS");
  const [srcId, setSrcId] = useState("");
  const [dstId, setDstId] = useState("");
  const [editTarget, setEditTarget] = useState<NodeDto | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");

  const servicesQ = useQuery({ queryKey: ["services", offset], queryFn: () => listServices(PAGE, offset) });
  const apisQ = useQuery({ queryKey: ["apis", offset], queryFn: () => listApis(PAGE, offset) });
  const relsQ = useQuery({
    queryKey: ["manual-relationships", offset],
    queryFn: () => listManualRelationships(PAGE, offset),
  });

  const createServiceM = useMutation({
    mutationFn: () => createService(name, description, tier),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["services"] });
      setName("");
      setTier("");
      setDescription("");
    },
  });
  const createApiM = useMutation({
    mutationFn: () => createApi(apiName, apiMethod, apiPath, ""),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["apis"] });
      setApiName("");
      setApiPath("");
    },
  });
  const createRelM = useMutation({
    mutationFn: () => createRelationship(relType, srcId, dstId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["manual-relationships"] });
      qc.invalidateQueries({ queryKey: ["relationships"] });
      setSrcId("");
      setDstId("");
    },
  });
  const updateServiceM = useMutation({
    mutationFn: () =>
      updateService(editTarget?.entityId ?? "", { name: editName, description: editDescription }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["services"] });
      setEditTarget(null);
    },
  });
  const deleteRelM = useMutation({
    mutationFn: (edge: EdgeDto) => deleteRelationship(edge.relType, edge.srcId, edge.dstId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["manual-relationships"] });
      qc.invalidateQueries({ queryKey: ["relationships"] });
    },
  });

  // Structural: any mutation object exposes `mutate()`; keeps the helper generic-free.
  function submit(form: FormEvent, mutation: { mutate: () => void }) {
    form.preventDefault();
    mutation.mutate();
  }

  return (
    <div className="grid">
      <Panel title="Services" note="EngGraph-managed · provenance is MANUAL">
        {servicesQ.isPending && <Loading label="loading services…" />}
        {servicesQ.isError && <ErrorState error={servicesQ.error} />}
        {servicesQ.data && servicesQ.data.items.length === 0 && (
          <EmptyState title="No services in the catalog" hint={isAdmin ? "Create one below." : "Ask an admin to create one."} />
        )}
        {servicesQ.data && servicesQ.data.items.length > 0 && (
          <>
            <ul className="result-list">
              {servicesQ.data.items.map((node) => (
                <li key={node.entityId} className="result">
                  <EntityLink node={node} />
                  <ProvenanceTag provenance="MANUAL" />
                  <span className="meta">
                    tier {String(node.properties.tier ?? "—")} · {String(node.properties.description ?? "")}
                  </span>
                  {isAdmin && (
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => {
                        setEditTarget(node);
                        setEditName(node.name);
                        setEditDescription(String(node.properties.description ?? ""));
                      }}
                    >
                      edit
                    </button>
                  )}
                </li>
              ))}
            </ul>
            <Pagination
              total={servicesQ.data.total}
              limit={servicesQ.data.limit}
              offset={servicesQ.data.offset}
              onPage={setOffset}
            />
          </>
        )}
      </Panel>

      {isAdmin && (
        <>
          <Panel title="Create a service" note="admin-only · stamped as configured by you">
            <form
              className="form-grid"
              onSubmit={(event) => submit(event, createServiceM)}
            >
              <label>
                Name
                <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required />
              </label>
              <label>
                Tier
                <input value={tier} onChange={(e) => setTier(e.target.value)} maxLength={60} />
              </label>
              <label>
                Description
                <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
              </label>
              <button type="submit" disabled={createServiceM.isPending}>
                {createServiceM.isPending ? "Creating…" : "Create service"}
              </button>
            </form>
            {createServiceM.isError && <ErrorState error={createServiceM.error} />}
            {createServiceM.isSuccess && (
              <p className="state ok">Created {createServiceM.data.name} · {createServiceM.data.entityId}</p>
            )}
            {editTarget && (
              <form
                className="form-grid"
                onSubmit={(event) => submit(event, updateServiceM)}
              >
                <h3>Editing {editTarget.entityId}</h3>
                <label>
                  Name
                  <input value={editName} onChange={(e) => setEditName(e.target.value)} maxLength={200} required />
                </label>
                <label>
                  Description
                  <input
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    maxLength={2000}
                  />
                </label>
                <button type="submit" disabled={updateServiceM.isPending}>
                  {updateServiceM.isPending ? "Saving…" : "Save changes"}
                </button>
                <button type="button" onClick={() => setEditTarget(null)}>
                  Cancel
                </button>
              </form>
            )}
            {updateServiceM.isError && <ErrorState error={updateServiceM.error} />}
          </Panel>

          <Panel title="Create an API" note="admin-only · endpoint contract">
            <form className="form-grid" onSubmit={(event) => submit(event, createApiM)}>
              <label>
                Name
                <input value={apiName} onChange={(e) => setApiName(e.target.value)} maxLength={200} required />
              </label>
              <label>
                Method
                <select value={apiMethod} onChange={(e) => setApiMethod(e.target.value)}>
                  {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Path
                <input value={apiPath} onChange={(e) => setApiPath(e.target.value)} maxLength={200} />
              </label>
              <button type="submit" disabled={createApiM.isPending}>
                {createApiM.isPending ? "Creating…" : "Create API"}
              </button>
            </form>
            {createApiM.isError && <ErrorState error={createApiM.error} />}
            {createApiM.isSuccess && (
              <p className="state ok">
                Created {createApiM.data.name} · {createApiM.data.entityId}
              </p>
            )}
          </Panel>

          <Panel
            title="Configure a link"
            note="only manual types: OWNS, IMPLEMENTED_BY, EXPOSES, CONSUMES, DEPENDS_ON"
          >
            <form className="form-grid" onSubmit={(event) => submit(event, createRelM)}>
              <label>
                Type
                <select value={relType} onChange={(e) => setRelType(e.target.value)}>
                  {MANUAL_REL_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Source entity id
                <input
                  value={srcId}
                  onChange={(e) => setSrcId(e.target.value)}
                  maxLength={160}
                  placeholder="ado:acme:Commerce:repository:r-uuid-1"
                  required
                />
              </label>
              <label>
                Target entity id
                <input
                  value={dstId}
                  onChange={(e) => setDstId(e.target.value)}
                  maxLength={160}
                  placeholder="ado:acme:team:t-uuid-1"
                  required
                />
              </label>
              <button type="submit" disabled={createRelM.isPending}>
                {createRelM.isPending ? "Writing…" : "Add link"}
              </button>
            </form>
            <p className="note">
              The server validates the pair against the relationship semantics, stamps the edge as
              configured by you, and records an audit entry. Provider-derived edges cannot be written
              here.
            </p>
            {createRelM.isError && <ErrorState error={createRelM.error} />}
            {createRelM.isSuccess && (
              <p className="state ok">
                {createRelM.data.relType} · {createRelM.data.srcId} → {createRelM.data.dstId}
              </p>
            )}
          </Panel>
        </>
      )}

      <Panel title="Configured links" note="MANUAL provenance only · audited">
        {relsQ.isPending && <Loading label="loading links…" />}
        {relsQ.isError && <ErrorState error={relsQ.error} />}
        {relsQ.data && relsQ.data.items.length === 0 && (
          <EmptyState title="No manual links" hint="Ownership and dependencies are added by admins." />
        )}
        {relsQ.data && relsQ.data.items.length > 0 && (
          <>
            <ul className="edge-list">
              {relsQ.data.items.map((edge) => (
                <li key={`${edge.relType}:${edge.srcId}:${edge.dstId}`}>
                  <EdgeRow edge={edge} />
                  {isAdmin && (
                    <button
                      type="button"
                      className="link-button danger"
                      disabled={deleteRelM.isPending}
                      onClick={() => deleteRelM.mutate(edge)}
                    >
                      remove
                    </button>
                  )}
                </li>
              ))}
            </ul>
            <p className="meta">
              Every link shows who created it and when. The server refuses deletes of provider-owned
              edges.
            </p>
            <Pagination
              total={relsQ.data.total}
              limit={relsQ.data.limit}
              offset={relsQ.data.offset}
              onPage={setOffset}
            />
          </>
        )}
        <Link className="link-button" to="/search">
          Find entity ids to link
        </Link>
      </Panel>
    </div>
  );
}
