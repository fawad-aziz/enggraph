import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { searchEntities } from "../api/client";
import { NODE_TYPES } from "../api/types";
import { EntityLink, SourceLink } from "../components/entities";
import { EmptyState, ErrorState, Loading, Pagination, Panel, TruncationNote } from "../components/ui";

const PAGE = 20;

export function SearchPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);

  const results = useQuery({
    queryKey: ["search", submitted, types, offset],
    queryFn: () => searchEntities(submitted, types, PAGE, offset),
    enabled: submitted.length > 0,
  });

  function toggle(type: string) {
    setTypes((prev) => (prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type]));
    setOffset(0);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(q.trim());
    setOffset(0);
  }

  return (
    <div className="grid">
      <Panel title="Search entities" note="free text · bounded, paginated, org-scoped">
        <form onSubmit={onSubmit} className="search-form">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="name, id fragment, service, repo…"
            maxLength={120}
            aria-label="search query"
          />
          <button type="submit" disabled={q.trim().length === 0}>
            Search
          </button>
        </form>
        <fieldset className="type-filter">
          <legend>Restrict to types (none checked = all)</legend>
          {NODE_TYPES.map((t) => (
            <label key={t} className="chip">
              <input
                type="checkbox"
                checked={types.includes(t)}
                onChange={() => toggle(t)}
              />
              {t}
            </label>
          ))}
        </fieldset>
      </Panel>

      <Panel title="Results" note={results.data ? `match "${results.data.query}"` : undefined}>
        {results.isFetching && <Loading label="searching…" />}
        {results.isPending && submitted.length > 0 && <Loading label="searching…" />}
        {results.isError && <ErrorState error={results.error} />}
        {submitted.length === 0 && (
          <p className="state empty">
            Enter a query.
            <span className="hint">Search matches inside your organization only.</span>
          </p>
        )}
        {results.data && results.data.items.length === 0 && (
          <EmptyState
            title={`No match for "${results.data.query}"`}
            hint="Try a shorter query, or widen the type filter."
          />
        )}
        {results.data && results.data.items.length > 0 && (
          <>
            <TruncationNote
              truncated={results.data.items.length >= results.data.limit}
              limit={results.data.limit}
            />
            <ul className="result-list">
              {results.data.items.map((node) => (
                <li key={node.entityId} className={`result${node.isDemoData ? " demo" : ""}`}>
                  <EntityLink node={node} />
                  <span className="meta">
                    {node.entityType} · {node.sourceSystem ?? "local"}
                    {node.isDemoData ? " · demo data" : ""}
                  </span>
                  <SourceLink node={node} />
                  <span className="row-actions">
                    <Link to={`/explorer?entity=${encodeURIComponent(node.entityId)}`}>explore</Link>
                    <Link to={`/impact?subject=${encodeURIComponent(node.entityId)}`}>impact</Link>
                  </span>
                </li>
              ))}
            </ul>
            <Pagination
              total={results.data.total}
              limit={results.data.limit}
              offset={results.data.offset}
              onPage={setOffset}
            />
          </>
        )}
        <button type="button" className="link-button" onClick={() => navigate("/explorer")}>
          Open the explorer with a picked entity
        </button>
      </Panel>
    </div>
  );
}
