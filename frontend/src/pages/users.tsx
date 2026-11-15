// Admin-only user provisioning. The form calls the real endpoint; the server
// checks the role, hashes the password (PBKDF2) and scopes the user to the
// admin's own organization. Nothing here can grant a role the server refuses.

import { useState, type FormEvent } from "react";
import { createUser } from "../api/client";
import type { CreatedUser } from "../api/types";
import { ErrorState, Panel, ProvenanceTag } from "../components/ui";

const ROLES = ["admin", "viewer"] as const;

export function NewUserForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "viewer">("viewer");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<CreatedUser | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setCreated(null);
    setFailure(null);
    try {
      setCreated(await createUser(email, password, role));
      setPassword("");
    } catch (exc) {
      setFailure(exc);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Add a user" note="admin-only · scoped to your organization">
      <form onSubmit={onSubmit} className="form-grid">
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            maxLength={200}
          />
        </label>
        <label>
          Password (min 8)
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
            autoComplete="new-password"
          />
        </label>
        <label>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as "admin" | "viewer")}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create user"}
        </button>
      </form>
      {failure !== null && <ErrorState error={failure} />}
      {created && (
        <p className="state ok">
          <ProvenanceTag provenance="MANUAL" />
          <span>
            created {created.email} · role {created.role} in {created.organizationId} (the password is
            stored only as a hash)
          </span>
        </p>
      )}
    </Panel>
  );
}
