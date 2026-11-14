// Shared UI states: loading, empty, error, permission-denied, truncation,
// demo badge, pagination. Every screen uses these so no state is silent.

import type { ReactNode } from "react";
import { ApiFailure } from "../api/client";

export const PROVENANCE_LABELS: Record<string, string> = {
  PROVIDER_EXPLICIT: "imported from provider",
  MANUAL: "configured by a user",
  STATIC_ANALYSIS: "static analysis",
  RUNTIME_OBSERVED: "observed at runtime",
  INFERRED: "inferred (human review required)",
};

export function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return iso.replace("T", " ").replace("Z", " UTC");
}

export function Panel({
  title,
  note,
  actions,
  children,
}: {
  title: string;
  note?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {note && <span className="panel-note">{note}</span>}
        {actions && <div className="panel-actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <p className="state" role="status">
      {label}
    </p>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <p className="state empty">
      {title}
      {hint && <span className="hint">{hint}</span>}
    </p>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  if (error instanceof ApiFailure) {
    const denied = error.status === 403 || error.code === "admin_required";
    return (
      <p className="state error" role="alert">
        <strong>{denied ? "Permission denied" : "Request failed"}</strong>
        <span>{error.message}</span>
        <span className="meta">
          code: {error.code} · status: {error.status}
          {error.correlationId ? ` · correlation: ${error.correlationId}` : ""}
        </span>
        {denied && <span className="meta">Server-side role check; the UI cannot grant access.</span>}
      </p>
    );
  }
  return (
    <p className="state error" role="alert">
      <strong>Request failed</strong>
      <span>{String(error)}</span>
    </p>
  );
}

export function TruncationNote({
  truncated,
  limit,
  depth,
}: {
  truncated: boolean;
  limit: number;
  depth?: number;
}) {
  if (!truncated) return null;
  return (
    <p className="state warn" role="status">
      Result hit the{" "}
      {depth !== undefined ? (
        <>depth {depth} / limit {limit}</>
      ) : (
        <>limit {limit}</>
      )}{" "}
      bound — this is a bounded view, not a complete set. Narrow the filters or
      raise the bound within the configured ceiling.
    </p>
  );
}

export function DemoBadge() {
  return <span className="demo-badge">DEMO DATA</span>;
}

export function ProvenanceTag({ provenance }: { provenance: string | null }) {
  if (!provenance) return null;
  return (
    <span className={`tag prov-${provenance.toLowerCase()}`}>
      {PROVENANCE_LABELS[provenance] ?? provenance}
    </span>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onPage,
}: {
  total: number;
  limit: number;
  offset: number;
  onPage: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <nav className="pagination">
      <button type="button" disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))}>
        Previous
      </button>
      <span>
        page {page} of {pages} · {total} record{total === 1 ? "" : "s"}
      </span>
      <button
        type="button"
        disabled={offset + limit >= total}
        onClick={() => onPage(offset + limit)}
      >
        Next
      </button>
    </nav>
  );
}
