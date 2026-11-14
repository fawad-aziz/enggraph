// Azure DevOps connector configuration (FR-02, FR-11). The form submits a
// *reference* to the PAT (an environment variable name), never the secret.
// Validation issues exactly one live request to dev.azure.com through the same
// client the sync worker uses; failures are surfaced verbatim, with the
// correlation id, so a misconfiguration is diagnosable from the UI.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { configureConnector, providerProjects, validateConnector } from "../api/client";
import type { ConnectorDto } from "../api/types";
import { EmptyState, ErrorState, fmtTime, Loading, Panel } from "../components/ui";
import { useIsAdmin, useSession } from "../session";

export function ConnectorsPage() {
  const isAdmin = useIsAdmin();
  const { principal } = useSession();
  const qc = useQueryClient();
  const [adoOrg, setAdoOrg] = useState("");
  const [secretRef, setSecretRef] = useState("AZURE_DEVOPS_PAT");
  const [projectNames, setProjectNames] = useState("");
  const [pageSize, setPageSize] = useState(100);

  const validateM = useMutation({
    mutationFn: () => validateConnector(adoOrg.trim(), secretRef.trim()),
  });
  const configureM = useMutation({
    mutationFn: () =>
      configureConnector(
        adoOrg.trim(),
        secretRef.trim(),
        projectNames.split(",").map((n) => n.trim()).filter((n) => n.length > 0),
        pageSize,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const projectsQ = useQuery({
    queryKey: ["projects"],
    queryFn: providerProjects,
    enabled: isAdmin && Boolean(adoOrg),
    retry: false,
  });

  function sharedFieldset(onValidate: () => void) {
    return (
      <>
        <label>
          Azure DevOps organization
          <input
            value={adoOrg}
            onChange={(e) => setAdoOrg(e.target.value)}
            maxLength={100}
            placeholder="contoso"
            required
          />
        </label>
        <label>
          PAT secret reference (environment variable name)
          <input value={secretRef} onChange={(e) => setSecretRef(e.target.value)} maxLength={80} required />
        </label>
        <button type="button" onClick={onValidate} disabled={validateM.isPending}>
          {validateM.isPending ? "Validating…" : "Validate connection"}
        </button>
      </>
    );
  }

  return (
    <div className="grid">
      {isAdmin ? (
        <Panel title="Validate" note="exactly one request to dev.azure.com">
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              validateM.mutate();
            }}
          >
            {sharedFieldset(() => validateM.mutate())}
          </form>
          {validateM.isError && <ErrorState error={validateM.error} />}
          {validateM.isSuccess && (
            <p className={validateM.data.reachable ? "state ok" : "state error"} role="status">
              <strong>{validateM.data.reachable ? "reachable" : "not reachable"}</strong>
              <span>
                organization {validateM.data.organization} · secret reference{" "}
                {validateM.data.secretReference} (the value is never returned)
              </span>
              {validateM.data.sampleProjectNames.length > 0 && (
                <span>sample projects: {validateM.data.sampleProjectNames.join(", ")}</span>
              )}
            </p>
          )}
        </Panel>
      ) : (
        <Panel title="Validate" note="admin-only">
          <p className="state empty">
            Connector configuration is admin-only.
            <span className="hint">Your role is {principal?.role}; you can read the graph.</span>
          </p>
        </Panel>
      )}

      {isAdmin && (
        <Panel title="Configure" note="stores the secret reference, not the secret">
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              configureM.mutate();
            }}
          >
            {sharedFieldset(() => validateM.mutate())}
            <label>
              Project names (comma-separated)
              <input
                value={projectNames}
                onChange={(e) => setProjectNames(e.target.value)}
                maxLength={2000}
                placeholder="Commerce, Billing"
                required
              />
            </label>
            <label>
              Page size {pageSize}
              <input
                type="range"
                min={10}
                max={200}
                step={10}
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
              />
            </label>
            <button type="submit" disabled={configureM.isPending}>
              {configureM.isPending ? "Storing…" : "Validate and store"}
            </button>
          </form>
          {configureM.isError && <ErrorState error={configureM.error} />}
          {configureM.isSuccess && <ConnectorSummary connector={configureM.data} />}
        </Panel>
      )}

      <Panel title="Live project list" note="read from the configured connector, not cached">
        {!isAdmin && <p className="state empty">Sign in as an admin to read the live project list.</p>}
        {isAdmin && projectsQ.isPending && adoOrg.length > 0 && <Loading label="reading projects…" />}
        {isAdmin && projectsQ.isError && <ErrorState error={projectsQ.error} />}
        {isAdmin && projectsQ.data && projectsQ.data.items.length === 0 && (
          <EmptyState title="The connector returned no projects" hint="Check the PAT scope and the organization name." />
        )}
        {isAdmin && projectsQ.data && projectsQ.data.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>name</th>
                <th>id</th>
                <th>state</th>
              </tr>
            </thead>
            <tbody>
              {projectsQ.data.items.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>{p.id}</td>
                  <td>{p.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}

function ConnectorSummary({ connector }: { connector: ConnectorDto }) {
  return (
    <dl className="kv">
      <div>
        <dt>connector id</dt>
        <dd>{connector.id}</dd>
      </div>
      <div>
        <dt>ado organization</dt>
        <dd>{connector.adoOrganization}</dd>
      </div>
      <div>
        <dt>secret reference</dt>
        <dd>{connector.patSecretRef}</dd>
      </div>
      <div>
        <dt>projects</dt>
        <dd>{connector.projectNames.join(", ")}</dd>
      </div>
      <div>
        <dt>last validation</dt>
        <dd className={connector.lastValidationOk ? "ok" : "bad"}>
          {fmtTime(connector.lastValidatedAt)} · {connector.lastValidationOk ? "ok" : "failed"}
        </dd>
      </div>
    </dl>
  );
}
