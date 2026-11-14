import { useQuery } from "@tanstack/react-query";
import { health, listJobs, organizations, readiness } from "../api/client";
import { ErrorState, fmtTime, Loading, Panel } from "../components/ui";
import { useIsAdmin, useSession } from "../session";
import { NewUserForm } from "./users";

export function DashboardPage() {
  const { principal } = useSession();
  const isAdmin = useIsAdmin();

  const healthQ = useQuery({ queryKey: ["health"], queryFn: health });
  const readyQ = useQuery({ queryKey: ["readiness"], queryFn: readiness });
  const orgQ = useQuery({ queryKey: ["organizations"], queryFn: organizations });
  const jobsQ = useQuery({ queryKey: ["jobs", 5, 0], queryFn: () => listJobs(5, 0) });

  const org = orgQ.data?.items[0];
  const lastSucceeded = jobsQ.data?.items.find((j) => j.state === "SUCCEEDED");

  return (
    <div className="grid">
      <Panel title="System" note="backend + stores">
        {healthQ.isPending && <Loading label="checking health…" />}
        {healthQ.isError && <ErrorState error={healthQ.error} />}
        {readyQ.isError && <ErrorState error={readyQ.error} />}
        {healthQ.data && readyQ.data && (
          <>
            <dl className="kv">
              <div>
                <dt>api</dt>
                <dd>
                  {healthQ.data.status} · v{healthQ.data.version} · {healthQ.data.mode} mode
                </dd>
              </div>
              <div>
                <dt>readiness</dt>
                <dd>{readyQ.data.status}</dd>
              </div>
              {Object.entries(readyQ.data.components).map(([name, state]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd className={state === "ok" ? "ok" : "bad"}>{state}</dd>
                </div>
              ))}
            </dl>
            {readyQ.data.config_problems.length > 0 && (
              <ul className="problems">
                {readyQ.data.config_problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </Panel>

      <Panel title="Organization" note={org ? org.displayName : undefined}>
        {!principal && <Loading label="sign in to load your organization" />}
        {orgQ.isPending && principal && <Loading label="loading organization…" />}
        {orgQ.isError && <ErrorState error={orgQ.error} />}
        {org && (
          <>
            <dl className="kv">
              <div>
                <dt>organization id</dt>
                <dd>{org.organizationId}</dd>
              </div>
              <div>
                <dt>you</dt>
                <dd>
                  {principal?.email} · role {principal?.role}
                </dd>
              </div>
              <div>
                <dt>query ceiling</dt>
                <dd>depth {org.maxDepth} · results clamped by server config</dd>
              </div>
            </dl>
            {Object.keys(org.graphCounts).length === 0 ? (
              <p className="state empty">
                No graph data yet.
                <span className="hint">Configure a connector and run a sync, or load demo data.</span>
              </p>
            ) : (
              <ul className="counts">
                {Object.entries(org.graphCounts).map(([label, count]) => (
                  <li key={label}>
                    <span className="etype">{label}</span>
                    <span className="count">{count}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Panel>

      <Panel title="Sync activity" note="most recent jobs">
        {jobsQ.isPending && <Loading label="loading jobs…" />}
        {jobsQ.isError && <ErrorState error={jobsQ.error} />}
        {jobsQ.data && jobsQ.data.items.length === 0 && (
          <p className="state empty">
            No sync jobs recorded.
            <span className="hint">Queue one from the Sync page.</span>
          </p>
        )}
        {jobsQ.data && jobsQ.data.items.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>state</th>
                <th>mode</th>
                <th>nodes / edges</th>
                <th>finished</th>
              </tr>
            </thead>
            <tbody>
              {jobsQ.data.items.map((job) => (
                <tr key={job.jobId}>
                  <td className={`state-${job.state.toLowerCase()}`}>{job.state}</td>
                  <td>{job.mode}</td>
                  <td>
                    {job.nodesWritten} / {job.edgesWritten}
                  </td>
                  <td>{fmtTime(job.finishedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {lastSucceeded && (
          <p className="meta">
            last successful sync {fmtTime(lastSucceeded.finishedAt)} · {lastSucceeded.nodesWritten} nodes
          </p>
        )}
      </Panel>

      {isAdmin && <NewUserForm />}
    </div>
  );
}
