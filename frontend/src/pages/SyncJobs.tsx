// Sync jobs (FR-02, FR-10). Jobs are observed through the API: state, attempt,
// write counts and the per-stage event log. Retry creates a *new* job linked to
// the failed parent, so history is never overwritten. Enqueue, retry and cancel
// are admin-only, enforced by the server.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelJob, enqueueJob, getJob, listJobs, retryJob } from "../api/client";
import type { JobProgress } from "../api/types";
import { EmptyState, ErrorState, fmtTime, Loading, Panel, Pagination } from "../components/ui";
import { useIsAdmin } from "../session";

const PAGE = 20;

export function SyncJobsPage() {
  const isAdmin = useIsAdmin();
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [openJob, setOpenJob] = useState<string | null>(null);
  const [connectorId, setConnectorId] = useState("");
  const [mode, setMode] = useState<"FULL" | "INCREMENTAL">("INCREMENTAL");

  const jobsQ = useQuery({ queryKey: ["jobs", PAGE, offset], queryFn: () => listJobs(PAGE, offset) });
  const detailQ = useQuery({
    queryKey: ["job", openJob],
    queryFn: () => getJob(openJob ?? ""),
    enabled: openJob !== null,
    refetchInterval: 2000,
  });

  const onJobDone = () => {
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: ["organizations"] });
  };

  const enqueueM = useMutation({ mutationFn: () => enqueueJob(connectorId.trim(), mode), onSuccess: onJobDone });
  const retryM = useMutation({ mutationFn: (id: string) => retryJob(id), onSuccess: onJobDone });
  const cancelM = useMutation({ mutationFn: (id: string) => cancelJob(id), onSuccess: onJobDone });

  return (
    <div className="grid">
      <Panel title="Jobs" note="most recent first · org-scoped">
        {jobsQ.isPending && <Loading label="loading jobs…" />}
        {jobsQ.isError && <ErrorState error={jobsQ.error} />}
        {jobsQ.data && jobsQ.data.items.length === 0 && (
          <EmptyState title="No sync jobs" hint="Queue one below (admin)." />
        )}
        {jobsQ.data && jobsQ.data.items.length > 0 && (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>state</th>
                  <th>mode</th>
                  <th>attempt</th>
                  <th>projects</th>
                  <th>nodes / edges</th>
                  <th>created</th>
                  <th>actions</th>
                </tr>
              </thead>
              <tbody>
                {jobsQ.data.items.map((job) => (
                  <tr key={job.jobId}>
                    <td className={`state-${job.state.toLowerCase()}`}>{job.state}</td>
                    <td>{job.mode}</td>
                    <td>{job.attempt}</td>
                    <td>
                      {job.projectsCompleted} / {job.projectsPlanned}
                    </td>
                    <td>
                      {job.nodesWritten} / {job.edgesWritten}
                    </td>
                    <td>{fmtTime(job.createdAt)}</td>
                    <td>
                      <button type="button" onClick={() => setOpenJob(job.jobId)}>
                        detail
                      </button>
                      {isAdmin && (job.state === "FAILED" || job.state === "CANCELED") && (
                        <button type="button" disabled={retryM.isPending} onClick={() => retryM.mutate(job.jobId)}>
                          retry
                        </button>
                      )}
                      {isAdmin && (job.state === "QUEUED" || job.state === "RUNNING") && (
                        <button
                          type="button"
                          disabled={cancelM.isPending}
                          onClick={() => cancelM.mutate(job.jobId)}
                        >
                          cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination
              total={jobsQ.data.total}
              limit={jobsQ.data.limit}
              offset={jobsQ.data.offset}
              onPage={setOffset}
            />
          </>
        )}
      </Panel>

      {isAdmin && (
        <Panel title="Queue a sync" note="admin-only · a queued job writes into the graph">
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              enqueueM.mutate();
            }}
          >
            <label>
              Connector config id
              <input
                value={connectorId}
                onChange={(e) => setConnectorId(e.target.value)}
                maxLength={40}
                placeholder="uuid from the Connectors page"
                required
              />
            </label>
            <label>
              Mode
              <select value={mode} onChange={(e) => setMode(e.target.value as "FULL" | "INCREMENTAL")}>
                <option value="FULL">FULL</option>
                <option value="INCREMENTAL">INCREMENTAL</option>
              </select>
            </label>
            <button type="submit" disabled={enqueueM.isPending}>
              {enqueueM.isPending ? "Queuing…" : "Queue job"}
            </button>
          </form>
          {enqueueM.isError && <ErrorState error={enqueueM.error} />}
          {enqueueM.isSuccess && (
            <p className="state ok">
              Queued {enqueueM.data.jobId} · state {enqueueM.data.state}
            </p>
          )}
          <p className="note">
            A checkpoint advances only after a batch is persisted, so an interrupted run resumes where
            it stopped. Reconciliation runs only on a complete pass.
          </p>
        </Panel>
      )}

      <JobDetail job={detailQ.data} />
      {detailQ.isError && (
        <Panel title="Job detail">
          <ErrorState error={detailQ.error} />
        </Panel>
      )}
    </div>
  );
}

function JobDetail({ job }: { job: JobProgress | undefined }) {
  if (!job) return null;
  const retryable = job.state === "FAILED" || job.state === "CANCELED";
  const cancellable = job.state === "QUEUED" || job.state === "RUNNING";
  return (
    <Panel
      title={`Job ${job.jobId}`}
      note={`${job.state} · attempt ${job.attempt} · worker ${job.workerId ?? "—"}`}
      actions={<span className="meta">refreshed every 2s while open</span>}
    >
      <dl className="kv">
        <div>
          <dt>organization</dt>
          <dd>{job.organizationId}</dd>
        </div>
        <div>
          <dt>mode</dt>
          <dd>{job.mode}</dd>
        </div>
        <div>
          <dt>parent job</dt>
          <dd>{job.parentJobId ?? "—"} {job.parentJobId ? "(this job is a retry)" : ""}</dd>
        </div>
        <div>
          <dt>requested by</dt>
          <dd>{job.requestedBy ?? "—"}</dd>
        </div>
        <div>
          <dt>writes</dt>
          <dd>
            {job.nodesWritten} nodes · {job.edgesWritten} edges
          </dd>
        </div>
        <div>
          <dt>projects</dt>
          <dd>
            {job.projectsCompleted} / {job.projectsPlanned}
          </dd>
        </div>
        <div>
          <dt>started / finished</dt>
          <dd>
            {fmtTime(job.startedAt)} → {fmtTime(job.finishedAt)}
          </dd>
        </div>
        {job.error && (
          <div>
            <dt>error</dt>
            <dd className="bad">{job.error}</dd>
          </div>
        )}
      </dl>
      <h3>Event log</h3>
      {job.events.length === 0 ? (
        <p className="state empty">No events recorded yet.</p>
      ) : (
        <ol className="events">
          {job.events.map((event) => (
            <li key={event.seq} className={`event-${event.level.toLowerCase()}`}>
              <span className="seq">{event.seq}</span>
              <span className="stage">{event.stage}</span>
              <span className="level">{event.level}</span>
              <span className="message">{event.message}</span>
            </li>
          ))}
        </ol>
      )}
      <p className="meta">
        {retryable ? "retryable (creates a new job linked to this one)" : "not retryable in this state"} ·{" "}
        {cancellable ? "cancellable" : "not cancellable in this state"}
      </p>
    </Panel>
  );
}
