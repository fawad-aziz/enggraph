// API service module: all backend calls go through here.
// The browser talks only to the backend API (never databases directly), and the
// server — not this file — is what enforces authorization.

import type {
  ConnectorDto,
  CreatedUser,
  EdgeDto,
  Envelope,
  HealthResponse,
  ImpactReport,
  JobProgress,
  LoginResponse,
  NeighborhoodResponse,
  NodeDto,
  OrganizationItem,
  PathsResponse,
  Principal,
  ReadinessResponse,
  RelationshipsResponse,
  ValidateResponse,
} from "./types";

const BASE = "/api/v1";
const TOKEN_KEY = "enggraph_token";

export class ApiFailure extends Error {
  code: string;
  status: number;
  correlationId: string | null;

  constructor(code: string, message: string, status: number, correlationId: string | null) {
    super(message);
    this.name = "ApiFailure";
    this.code = code;
    this.status = status;
    this.correlationId = correlationId;
  }
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const resp = await fetch(`${BASE}${path}`, { ...options, headers });
  const text = await resp.text();
  if (!resp.ok) {
    let code = "unknown";
    let message = text.slice(0, 200) || `request failed (${resp.status})`;
    let cid: string | null = resp.headers.get("x-correlation-id");
    try {
      const parsed = JSON.parse(text) as { error?: { code?: string; message?: string; correlationId?: string } };
      if (parsed.error) {
        code = parsed.error.code ?? code;
        message = parsed.error.message ?? message;
        cid = parsed.error.correlationId ?? cid;
      }
    } catch {
      // Non-JSON failure body: keep the truncated text as the message.
    }
    throw new ApiFailure(code, message, resp.status, cid);
  }
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

function qs(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    parts.push(`${key}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// ---------------------------------------------------------------- health / auth

export function health(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>("/health");
}

export function readiness(): Promise<ReadinessResponse> {
  return apiRequest<ReadinessResponse>("/health/ready");
}

export function login(email: string, password: string): Promise<LoginResponse> {
  return apiRequest<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function me(): Promise<Principal> {
  return apiRequest<Principal>("/auth/me");
}

export function logout(): Promise<{ revoked: boolean }> {
  return apiRequest<{ revoked: boolean }>("/auth/logout", { method: "POST" });
}

export function createUser(email: string, password: string, role: string): Promise<CreatedUser> {
  return apiRequest<CreatedUser>("/auth/users", {
    method: "POST",
    body: JSON.stringify({ email, password, role }),
  });
}

// ---------------------------------------------------------------- organizations

export function organizations(): Promise<Envelope<OrganizationItem>> {
  return apiRequest<Envelope<OrganizationItem>>("/organizations");
}

// ---------------------------------------------------------------- connectors

export function validateConnector(adoOrganization: string, patSecretRef: string): Promise<ValidateResponse> {
  return apiRequest<ValidateResponse>("/connectors/azure-devops/validate", {
    method: "POST",
    body: JSON.stringify({ adoOrganization, patSecretRef }),
  });
}

export function configureConnector(
  adoOrganization: string,
  patSecretRef: string,
  projectNames: string[],
  pageSize: number,
): Promise<ConnectorDto> {
  return apiRequest<ConnectorDto>("/connectors/azure-devops/configure", {
    method: "POST",
    body: JSON.stringify({ adoOrganization, patSecretRef, projectNames, pageSize }),
  });
}

export function providerProjects(): Promise<Envelope<{ name: string; id: string; state: string }>> {
  return apiRequest<Envelope<{ name: string; id: string; state: string }>>("/connectors/azure-devops/projects");
}

// ---------------------------------------------------------------- sync jobs

export function enqueueJob(connectorConfigId: string, mode: string): Promise<JobProgress> {
  return apiRequest<JobProgress>("/sync-jobs", {
    method: "POST",
    body: JSON.stringify({ connectorConfigId, mode }),
  });
}

export function listJobs(limit: number, offset: number): Promise<Envelope<JobProgress>> {
  return apiRequest<Envelope<JobProgress>>(`/sync-jobs${qs({ limit, offset })}`);
}

export function getJob(jobId: string): Promise<JobProgress> {
  return apiRequest<JobProgress>(`/sync-jobs/${encodeURIComponent(jobId)}`);
}

export function retryJob(jobId: string): Promise<JobProgress> {
  return apiRequest<JobProgress>(`/sync-jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
}

export function cancelJob(jobId: string): Promise<JobProgress> {
  return apiRequest<JobProgress>(`/sync-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
}

// ---------------------------------------------------------------- search / entities

export function searchEntities(
  q: string,
  types: string[],
  limit: number,
  offset: number,
): Promise<Envelope<NodeDto> & { query: string; types: string[] }> {
  return apiRequest<Envelope<NodeDto> & { query: string; types: string[] }>(
    `/search${qs({ q, types: types.join(","), limit, offset })}`,
  );
}

export function getEntity(entityId: string): Promise<NodeDto> {
  return apiRequest<NodeDto>(`/entities/${encodeURIComponent(entityId)}`);
}

export function entityRelationships(entityId: string, limit: number): Promise<RelationshipsResponse> {
  return apiRequest<RelationshipsResponse>(
    `/entities/${encodeURIComponent(entityId)}/relationships${qs({ limit })}`,
  );
}

export function neighborhood(
  entityId: string,
  depth: number,
  limit: number,
  nodeTypes: string[],
  relTypes: string[],
): Promise<NeighborhoodResponse> {
  return apiRequest<NeighborhoodResponse>(
    `/entities/${encodeURIComponent(entityId)}/neighborhood${qs({
      depth,
      limit,
      node_types: nodeTypes.join(","),
      rel_types: relTypes.join(","),
    })}`,
  );
}

export function pathsBetween(
  entityId: string,
  to: string,
  depth: number,
  limit: number,
  relTypes: string[],
): Promise<PathsResponse> {
  return apiRequest<PathsResponse>(
    `/entities/${encodeURIComponent(entityId)}/paths${qs({
      to,
      depth,
      limit,
      rel_types: relTypes.join(","),
    })}`,
  );
}

// ---------------------------------------------------------------- catalog

export function listServices(limit: number, offset: number): Promise<Envelope<NodeDto>> {
  return apiRequest<Envelope<NodeDto>>(`/services${qs({ limit, offset })}`);
}

export function listApis(limit: number, offset: number): Promise<Envelope<NodeDto>> {
  return apiRequest<Envelope<NodeDto>>(`/apis${qs({ limit, offset })}`);
}

export function listManualRelationships(limit: number, offset: number): Promise<Envelope<EdgeDto>> {
  return apiRequest<Envelope<EdgeDto>>(`/relationships${qs({ limit, offset })}`);
}

export function createService(name: string, description: string, tier: string): Promise<NodeDto> {
  return apiRequest<NodeDto>("/services", {
    method: "POST",
    body: JSON.stringify({ name, description, tier }),
  });
}

export function updateService(
  serviceId: string,
  patch: { name?: string; description?: string; tier?: string },
): Promise<NodeDto> {
  return apiRequest<NodeDto>(`/services/${encodeURIComponent(serviceId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function createApi(
  name: string,
  method: string,
  path: string,
  description: string,
): Promise<NodeDto> {
  return apiRequest<NodeDto>("/apis", {
    method: "POST",
    body: JSON.stringify({ name, method, path, description }),
  });
}

export function createRelationship(relType: string, srcId: string, dstId: string): Promise<EdgeDto> {
  return apiRequest<EdgeDto>("/relationships", {
    method: "POST",
    body: JSON.stringify({ relType, srcId, dstId }),
  });
}

export function deleteRelationship(relType: string, srcId: string, dstId: string): Promise<{
  deleted: boolean;
  relType: string;
  srcId: string;
  dstId: string;
}> {
  return apiRequest<{ deleted: boolean; relType: string; srcId: string; dstId: string }>(
    `/relationships${qs({ relType, srcId, dstId })}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------- impact analysis

export function analyzeImpact(
  subjectId: string,
  categories: string[],
  depth: number,
  limit: number,
): Promise<ImpactReport> {
  return apiRequest<ImpactReport>("/impact-analysis", {
    method: "POST",
    body: JSON.stringify({ subjectId, categories, depth, limit }),
  });
}

export function getAnalysis(analysisId: string): Promise<ImpactReport> {
  return apiRequest<ImpactReport>(`/impact-analysis/${encodeURIComponent(analysisId)}`);
}
