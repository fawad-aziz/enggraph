import { useQuery } from "@tanstack/react-query";
import { health } from "./api/client";

// Full portal pages arrive in milestone 6; this is the working skeleton
// (real health API call, real loading/error states).
export default function App() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["health"],
    queryFn: health,
  });
  return (
    <main className="app-shell">
      <h1>EngGraph</h1>
      {isPending && <p>Loading application state…</p>}
      {isError && <p role="alert">Backend unreachable: {String(error)}</p>}
      {data && (
        <p>
          API status: {data.status} (mode: <strong>{data.mode}</strong>)
          {data.mode === "demo" && <span className="demo-badge">DEMO DATA</span>}
        </p>
      )}
    </main>
  );
}
