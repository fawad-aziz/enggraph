// Session state: the bearer token lives in sessionStorage, the principal is
// resolved by the server (`/auth/me`), and the UI gates on it. Authorization
// decisions are made by the backend; this file only reflects them.

import { ApiFailure, getToken, login, logout, me, setToken } from "./api/client";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import type { Principal } from "./api/types";

interface SessionState {
  principal: Principal | null;
  signingIn: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  loginError: string | null;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) return;
    // A stored token is only a claim; the server decides whether it is valid.
    me()
      .then((p) => setPrincipal(p))
      .catch(() => {
        setToken(null);
        setPrincipal(null);
      });
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    setSigningIn(true);
    setLoginError(null);
    try {
      const resp = await login(email, password);
      setToken(resp.token);
      setPrincipal(resp);
    } catch (exc) {
      // The API answers "invalid email or password" for every credential
      // failure; the UI repeats that message and does not guess the cause.
      setLoginError(exc instanceof ApiFailure ? exc.message : String(exc));
      throw exc;
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await logout();
    } catch {
      // Session already expired/revoked server-side; clear the local token.
    }
    setToken(null);
    setPrincipal(null);
  }, []);

  const value: SessionState = { principal, signingIn, signIn, signOut, loginError };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (ctx === null) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}

export function useIsAdmin(): boolean {
  const { principal } = useSession();
  return principal?.role === "admin";
}
