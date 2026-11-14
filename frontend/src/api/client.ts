// API service module: all backend calls go through here.
// The browser talks only to the backend API (never databases directly).

export interface HealthResponse {
  status: string;
  version: string;
  mode: "demo" | "live";
}

const BASE = "/api/v1";

export function getToken(): string | null {
  return sessionStorage.getItem("enggraph_token");
}

export function setToken(token: string | null): void {
  if (token) sessionStorage.setItem("enggraph_token", token);
  else sessionStorage.removeItem("enggraph_token");
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const resp = await fetch(`${BASE}${path}`, { ...options, headers });
  if (resp.status === 401 || resp.status === 403) {
    throw new Error(`Permission denied (${resp.status}). Sign in with an authorized account.`);
  }
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`API error ${resp.status}: ${body.slice(0, 200)}`);
  }
  return (await resp.json()) as T;
}

export function health(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>("/health");
}
