// Change-impact analysis (FR-09). The report explains, for every entity, which
// relationship path put it in scope, how strong the evidence is, and what the
// bounds were. Limitations are rendered from the report, not invented here.

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { analyzeImpact, getAnalysis } from "../api/client";
import { IMPACT_CATEGORIES, type Finding } from "../api/types";
import { EntityLink } from "../components/entities";
import { EmptyState, ErrorState, fmtTime, Loading, Panel, ProvenanceTag, TruncationNote } from "../components/ui";

const CATEGORY_HINTS: Record<string, string> = {
  DIRECT: "any documented relationship touching the subject",
  DEPENDENCY: "DEPENDS_ON / CONSUMES / EXPOSES / IMPLEMENTED_BY",
  HISTORICAL: "commits, pull requests, and work items that changed the subject",
  OWNERSHIP: "teams, members, creators and reviewers",
  INFERRED: "one rule only: services that share an API (cross-consumer)",
};

export function ImpactPage() {
  const [params] = useSearchParams();
  const [subject, setSubject] = useState(params.get("subject") ?? "");
  const [depth, setDepth] = useState(4);
  const [limit, setLimit] = useState(100);
  const [categories, setCategories] = useState<string[]>([]);

  const analyzeM = useMutation({ mutationFn: () => analyzeImpact(subject.trim(), categories, depth, limit) });
  const report = analyzeM.data;

  const grouped = new Map<string, Finding[]>();
  if (report) {
    for (const finding of report.findings) {
      const bucket = grouped.get(finding.category) ?? [];
      bucket.push(finding);
      grouped.set(finding.category, bucket);
    }
  }

  const latestId = params.get("analysis");
  const latestQ = useQuery({
    queryKey: ["analysis", latestId],
    queryFn: () => getAnalysis(latestId ?? ""),
    enabled: Boolean(latestId),
  });

  return (
    <div className="grid">
      <Panel title="Subject" note="the thing that is changing">
        <form
          className="explorer-form"
          onSubmit={(event) => {
            event.preventDefault();
            analyzeM.mutate();
          }}
        >
          <label>
            Subject entity id
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={160}
              placeholder="ado:acme:Commerce:repository:r-uuid-1"
              required
            />
          </label>
          <label>
            depth {depth}
            <input
              type="range"
              min={1}
              max={10}
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
            />
          </label>
          <label>
            limit {limit}
            <input
              type="range"
              min={10}
              max={200}
              step={10}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
          </label>
          <fieldset>
            <legend>categories (none = all five)</legend>
            {IMPACT_CATEGORIES.map((c) => (
              <label key={c} className="chip">
                <input
                  type="checkbox"
                  checked={categories.includes(c)}
                  onChange={() =>
                    setCategories((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]))
                  }
                />
                {c}
              </label>
            ))}
          </fieldset>
          <button type="submit" disabled={analyzeM.isPending || subject.trim().length === 0}>
            {analyzeM.isPending ? "Analyzing…" : "Analyze impact"}
          </button>
        </form>
        {analyzeM.isError && <ErrorState error={analyzeM.error} />}
        <p className="note">
          The report is persisted in your organization and re-readable by id, so a decision can be
          referred back to the exact bounded snapshot it was taken from.
        </p>
      </Panel>

      {report && (
        <Panel
          title={`Impact report ${report.analysisId}`}
          note={`${report.subjectType} ${report.subjectName} · depth ${report.depth} · limit ${report.limit} · ${report.findings.length} finding(s)`}
          actions={<Link to={`/impact?subject=${encodeURIComponent(report.subjectId)}&analysis=${report.analysisId}`}>persisted id</Link>}
        >
          <dl className="kv">
            <div>
              <dt>requested by</dt>
              <dd>{report.requestedBy ?? "—"}</dd>
            </div>
            <div>
              <dt>created</dt>
              <dd>{fmtTime(report.createdAt)}</dd>
            </div>
            <div>
              <dt>counts by category</dt>
              <dd>{Object.entries(report.counts).map(([c, n]) => `${c} ${n}`).join(" · ")}</dd>
            </div>
          </dl>
          <TruncationNote truncated={report.truncated} limit={report.limit} depth={report.depth} />
          <h3>Limitations</h3>
          <ul className="limitations">
            {report.limitations.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="columns">
            <aside className="legend">
              <h3>Categories</h3>
              {IMPACT_CATEGORIES.map((c) => (
                <p key={c}>
                  <strong>{c}</strong>
                  <span>{CATEGORY_HINTS[c]}</span>
                </p>
              ))}
            </aside>
            <div>
              {report.findings.length === 0 && (
                <EmptyState
                  title="Nothing in scope inside the bound"
                  hint="Raise depth, add categories, and check the subject id."
                />
              )}
              {[...grouped.entries()].map(([category, findings]) => (
                <section key={category} className={`findings cat-${category.toLowerCase()}`}>
                  <h3>
                    {category} · {findings.length}
                  </h3>
                  <ul className="finding-list">
                    {findings.map((finding) => (
                      <li key={`${finding.category}:${finding.entityId}`}>
                        <EntityLink
                          node={{
                            entityId: finding.entityId,
                            name: finding.name,
                            entityType: finding.entityType,
                          }}
                        />
                        <span className="meta">
                          depth {finding.depth} · confidence {finding.confidence.toFixed(2)} ·{" "}
                          {finding.provenanceTypes.join(", ") || "no provenance recorded"}
                        </span>
                        {finding.hasManualEvidence && <ProvenanceTag provenance="MANUAL" />}
                        <span className="meta">last seen {fmtTime(finding.lastSeenAt)}</span>
                        {finding.sourceUrl ? (
                          <a
                            className="source"
                            href={finding.sourceUrl}
                            target="_blank"
                            rel="noreferrer noopener"
                          >
                            open source record
                          </a>
                        ) : (
                          <span className="source local">no provider link (managed in EngGraph)</span>
                        )}
                        <div className="path-steps">
                          {finding.path.entities.map((node, index) => (
                            <span key={node.entityId} className="step">
                              <EntityLink node={node} />
                              {index < finding.path.relationships.length && (
                                <span className="arrow">{finding.path.relationships[index].relType}</span>
                              )}
                            </span>
                          ))}
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </div>
        </Panel>
      )}

      {latestId && (
        <Panel title={`Reopened report ${latestId}`} note="read back from the persisted store">
          {latestQ.isPending && <Loading label="loading persisted report…" />}
          {latestQ.isError && <ErrorState error={latestQ.error} />}
          {latestQ.data && (
            <p className="state ok">
              {latestQ.data.subjectType} {latestQ.data.subjectName} · {latestQ.data.findings.length}{" "}
              finding(s) · created {fmtTime(latestQ.data.createdAt)}
            </p>
          )}
        </Panel>
      )}
    </div>
  );
}
