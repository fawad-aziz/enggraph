import { useQuery } from "@tanstack/react-query";
import { NavLink, Route, Routes } from "react-router-dom";
import { health } from "./api/client";
import { DemoBadge } from "./components/ui";
import { CatalogPage } from "./pages/Catalog";
import { ConnectorsPage } from "./pages/Connectors";
import { DashboardPage } from "./pages/Dashboard";
import { EntityDetailPage } from "./pages/EntityDetail";
import { ExplorerPage } from "./pages/Explorer";
import { ImpactPage } from "./pages/Impact";
import { LoginPage } from "./pages/Login";
import { SearchPage } from "./pages/Search";
import { SyncJobsPage } from "./pages/SyncJobs";
import { SessionProvider, useSession } from "./session";

const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/search", label: "Search" },
  { to: "/explorer", label: "Explorer" },
  { to: "/impact", label: "Impact" },
  { to: "/catalog", label: "Catalog" },
  { to: "/connectors", label: "Connectors" },
  { to: "/sync", label: "Sync jobs" },
];

export default function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  );
}

function Shell() {
  const { principal, signOut } = useSession();
  const healthQ = useQuery({ queryKey: ["health"], queryFn: health, refetchInterval: 30_000 });

  if (!principal) return <LoginPage />;

  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="brand">EngGraph</span>
        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <span className="topbar-right">
          {healthQ.data?.mode === "demo" && <DemoBadge />}
          <span className="who">
            {principal.email} · {principal.role} · {principal.organizationId}
          </span>
          <button type="button" onClick={() => signOut()}>
            sign out
          </button>
        </span>
      </header>
      {healthQ.isError && (
        <p className="state error" role="alert">
          The API is not answering. The pages below show the last state they loaded.
          <span className="meta">{String(healthQ.error)}</span>
        </p>
      )}
      <main className="content">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/explorer" element={<ExplorerPage />} />
          <Route path="/impact" element={<ImpactPage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="/connectors" element={<ConnectorsPage />} />
          <Route path="/sync" element={<SyncJobsPage />} />
          <Route path="/entities/:entityId" element={<EntityDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}
