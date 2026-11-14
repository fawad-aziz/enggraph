// DTO types mirroring docs/api.md (the API contract). camelCase, canonical ids.

export interface NodeDto {
  entityId: string;
  entityType: string;
  name: string;
  sourceSystem: string | null;
  sourceId: string | null;
  sourceUrl: string | null;
  lastSeenAt: string | null;
  isDemoData: boolean;
  properties: Record<string, unknown>;
}

export interface EdgeDto {
  relType: string;
  srcId: string;
  dstId: string;
  provenanceType: string | null;
  sourceSystem: string | null;
  sourceRecordId: string | null;
  observedAt: string | null;
  createdBy: string | null;
  properties: Record<string, unknown>;
}

export interface PathEntity {
  entityId: string;
  entityType: string;
  name: string;
}

export interface PathWitness {
  entities: PathEntity[];
  relationships: EdgeDto[];
}

export interface Envelope<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Principal {
  userId: string;
  email: string;
  role: string;
  organizationId: string;
  sessionId: string;
}

export interface LoginResponse extends Principal {
  token: string;
}

export interface CreatedUser {
  userId: string;
  email: string;
  role: string;
  organizationId: string;
  isActive: boolean;
}

export interface HealthResponse {
  status: string;
  version: string;
  mode: "demo" | "live";
}

export interface ReadinessResponse {
  status: "ok" | "degraded";
  components: Record<string, string>;
  config_problems: string[];
}

export interface OrganizationItem {
  organizationId: string;
  displayName: string;
  maxDepth: number;
  graphCounts: Record<string, number>;
  mode: "demo" | "live";
}

export interface ConnectorDto {
  id: string;
  organizationId: string;
  provider: string;
  adoOrganization: string;
  patSecretRef: string;
  projectNames: string[];
  pageSize: number;
  isActive: boolean;
  lastValidatedAt: string | null;
  lastValidationOk: boolean | null;
}

export interface ValidateResponse {
  organization: string;
  reachable: boolean;
  sampleProjectNames: string[];
  secretReference: string;
}

export interface JobDto {
  jobId: string;
  connectorConfigId: string;
  mode: string;
  state: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELED" | string;
  attempt: number;
  parentJobId: string | null;
  workerId: string | null;
  nodesWritten: number;
  edgesWritten: number;
  projectsCompleted: number;
  projectsPlanned: number;
  error: string | null;
  requestedBy: string | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface JobEvent {
  seq: number;
  stage: string;
  level: string;
  message: string;
}

export interface JobProgress extends JobDto {
  organizationId: string;
  events: JobEvent[];
}

export interface NeighborhoodResponse {
  subject: NodeDto;
  nodes: NodeDto[];
  edges: EdgeDto[];
  depth: number;
  limit: number;
  filters: { nodeTypes: string[]; relTypes: string[] };
  truncated: boolean;
}

export interface RelationshipsResponse {
  items: (EdgeDto & { direction: "outgoing" | "incoming" })[];
  total: number;
  limit: number;
  subjectId: string;
  truncated: boolean;
}

export interface PathsResponse {
  from: string;
  to: string;
  depth: number;
  limit: number;
  paths: PathWitness[];
  truncated: boolean;
}

export interface Finding {
  category: "DIRECT" | "DEPENDENCY" | "HISTORICAL" | "OWNERSHIP" | "INFERRED" | string;
  entityId: string;
  entityType: string;
  name: string;
  sourceUrl: string | null;
  depth: number;
  confidence: number;
  provenanceTypes: string[];
  hasManualEvidence: boolean;
  lastSeenAt: string | null;
  path: PathWitness;
}

export interface ImpactReport {
  analysisId: string;
  organizationId: string;
  subjectId: string;
  subjectType: string;
  subjectName: string;
  sourceUrl: string | null;
  requestedBy: string | null;
  categories: string[];
  depth: number;
  limit: number;
  createdAt: string;
  counts: Record<string, number>;
  findings: Finding[];
  truncated: boolean;
  limitations: string[];
}

// Mirrors `enggraph.graph.model.NODE_LABELS` (10 labels) and
// `RELATION_SEMANTICS` (18 types). Keep in sync with the backend.
export const NODE_TYPES = [
  "Organization",
  "Project",
  "Team",
  "Developer",
  "Repository",
  "WorkItem",
  "PullRequest",
  "Commit",
  "Service",
  "API",
] as const;

export const REL_TYPES = [
  "HAS_PROJECT",
  "HAS_TEAM",
  "HAS_MEMBER",
  "CONTAINS_REPOSITORY",
  "AUTHORED",
  "CONTAINS_COMMIT",
  "CONTAINS_PULL_REQUEST",
  "CREATED_BY",
  "REVIEWED_BY",
  "LINKED_TO",
  "CHANGES",
  "PARENT_OF",
  "RELATED_TO",
  "OWNS",
  "IMPLEMENTED_BY",
  "EXPOSES",
  "CONSUMES",
  "DEPENDS_ON",
] as const;

export const MANUAL_REL_TYPES = ["OWNS", "IMPLEMENTED_BY", "EXPOSES", "CONSUMES", "DEPENDS_ON"] as const;

export const IMPACT_CATEGORIES = ["DIRECT", "DEPENDENCY", "HISTORICAL", "OWNERSHIP", "INFERRED"] as const;
