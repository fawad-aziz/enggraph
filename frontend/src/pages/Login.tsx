import { useState, type FormEvent } from "react";
import { useSession } from "../session";

export function LoginPage() {
  const { signIn, signingIn, loginError } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      await signIn(email, password);
    } catch {
      // The message is surfaced from the session context; nothing to add here.
    }
  }

  return (
    <main className="login-page">
      <h1>EngGraph</h1>
      <p className="lede">
        Engineering graph: projects, repositories, work items, services, and the
        links between them. Every link carries its provenance.
      </p>
      <form onSubmit={onSubmit} className="login-form">
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            minLength={8}
            required
          />
        </label>
        <button type="submit" disabled={signingIn}>
          {signingIn ? "Signing in…" : "Sign in"}
        </button>
        {loginError && (
          <p className="state error" role="alert">
            {loginError}
          </p>
        )}
      </form>
      <p className="note">
        Sessions are bearer tokens held in this tab only. The server decides your
        role; viewers read, admins configure.
      </p>
    </main>
  );
}
