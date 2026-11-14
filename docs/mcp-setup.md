# MCP setup

EngGraph ships one MCP server with seven **read-only** tools. It is the same code
path as the REST API: every tool calls the domain services the API calls, resolves
the caller through the same session code, and reads only the caller's organization.
There is no second implementation of business logic, no write tool, and no way to
run graph statements.

## The tools

Every tool result is structured JSON. Common fields: `organizationId`,
`requestedBy`, the `depth` / `limit` actually used, `truncated`, `generatedAt`,
`relationshipSemantics`, `provenanceTypes`, `sourceUrls`, and a `caveats` list.
Read `caveats` before quoting a result: an empty list means "not found within the
bound", not "does not exist".

| Tool | Arguments (defaults) | What it reads |
|---|---|---|
| `search_engineering_entities` | `query`, `types` `()`, `limit` `50`, `offset` `0` | name/title search, optional type filter, `total` + `truncated` |
| `get_entity_context` | `entity_id`, `rel_types` `()`, `node_types` `()`, `depth` `4`, `limit` `50` | bounded neighborhood of one entity |
| `get_repository_context` | `repository_id`, `limit` `50` | commits, pull requests, linked work items, implementing services, owning teams, inferred contributors |
| `get_service_dependencies` | `service_id`, `direction` `"out"`, `depth` `4`, `limit` `50` | dependency paths, strictly in the requested direction (`Service` or `API` node) |
| `get_change_impact` | `entity_id`, `categories` `()`, `depth` `4`, `limit` `50` | findings grouped DIRECT / DEPENDENCY / HISTORICAL / OWNERSHIP / INFERRED, each with the path that explains it |
| `get_related_work_items` | `entity_id`, `depth` `2`, `limit` `50` | work items reached through `LINKED_TO`, `CHANGES`, `PARENT_OF`, `RELATED_TO` |
| `get_component_owners` | `entity_id`, `depth` `2`, `limit` `50` | `explicitOwners` (configured `OWNS`) and `inferredContributors` (authorship history, labelled as review-needed) |

`get_change_impact` persists its report as an audit record (`analysisId`) in
Postgres for the organization — the graph is never written by any tool.

Refusals arrive as `isError: true` with a readable `code: message`, not as a broken
connection, and the session keeps working: `missing_token`, `invalid_token`,
`invalid_request`, `entity_not_found`, `invalid_entity_type`,
`graph_unavailable`, `server_not_started`.

## Prerequisites

The MCP server reads the same configuration as the backend (`docs/development.md`):
`POSTGRES_DSN`, `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`,
`ENGGRAPH_SESSION_SECRET`, `ENGGRAPH_MAX_DEPTH` / `ENGGRAPH_MAX_RESULTS`. It needs
the same stores the API uses, with data in them (a configured connector and a
completed sync, `docs/api.md`).

A caller needs a **session token** — the same credential the API issues:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@your-org.test","password":"..."}'
# → {"token":"...","role":"admin","organizationId":"..."}
```

Sessions expire after `ENGGRAPH_SESSION_TTL_HOURS`.

## Running the server

```bash
cd backend
python -m enggraph.mcp stdio                        # the client launches this process
python -m enggraph.mcp http --host 127.0.0.1 --port 8100   # defaults: MCP_HOST, MCP_PORT
```

Configuration problems are logged and the process exits with code `2` **before** any
transport is opened, so a half-configured server never serves a partial surface.
`docker compose up` runs the `mcp-server` service (published on `127.0.0.1:8100`).

### Authentication

- **Streamable HTTP** — `Authorization: Bearer <token>` on every request, the same
  credential the API accepts, resolved by the same code. The organization a tool
  reads comes from the token; there is no organization argument.
- **stdio** — the client launches the process, so there is no per-request header.
  The operator supplies `ENGGRAPH_MCP_TOKEN` in the **server process** environment.
  If it is unset, the server starts and warns at startup that tool calls will be
  refused. A request over HTTP never inherits that process token.

A token is a credential: give it to the client out of band, keep it out of
committed files, logs and issues. `POST /api/v1/auth/logout` revokes a session;
a revoked token is refused (`invalid_token`).

## Client configuration

The server's contract is one contract; **where a client puts it differs by client** —
key names, file locations, and whether remote servers are supported at all are the
client's choice. Do not assume every client configures identically; check your
client's own MCP docs, and confirm with its MCP panel/inspector that the tools
appear. The two shapes below cover stdio-launching clients and HTTP-capable clients.

**stdio** (the client spawns the process; give it the environment it needs). `.env`
is read from the process's working directory, so `cwd` is where `.env` lives, and the
package must already be installed (`pip install -e backend[dev]`):

```json
{
  "mcpServers": {
    "enggraph": {
      "command": "python",
      "args": ["-m", "enggraph.mcp", "stdio"],
      "cwd": "/path/to/enggraph",
      "env": {
        "POSTGRES_DSN": "postgresql+psycopg://enggraph:…@localhost:5432/enggraph",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "…",
        "ENGGRAPH_SESSION_SECRET": "…",
        "ENGGRAPH_MCP_TOKEN": "session token from POST /api/v1/auth/login"
      }
    }
  }
}
```

Claude Desktop uses `mcpServers` in its config file; VS Code uses
`.vscode/mcp.json` with `"type": "stdio"`, `command`, `args`, `env`; Cursor uses
`.cursor/mcp.json` with `mcpServers`. Same contract, different files.

**Streamable HTTP** (clients that support remote servers): URL
`http://127.0.0.1:8100/mcp`, header `Authorization: Bearer <token>`, and an
`Accept` that allows both JSON and `text/event-stream` (the server is stateful by
default). A raw `curl` probe has to run the MCP handshake (`initialize` →
`notifications/initialized` → `tools/list`) and carry the session id the server
returns; the SDK client does that for you:

```python
import asyncio
from httpx2 import AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main() -> None:
    async with AsyncClient(headers={"Authorization": "Bearer TOKEN"}) as client:
        async with streamable_http_client("http://127.0.0.1:8100/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                for tool in (await session.list_tools()).tools:
                    print(tool.name, tool.annotations.read_only_hint)
                result = await session.call_tool(
                    "search_engineering_entities", {"query": "commerce", "limit": 5}
                )
                print(result.structured_content["items"])

asyncio.run(main())
```

## Limits the server enforces

- **Read-only.** Seven tools, no writes, no `run_cypher`, no user-supplied
  Cypher. Traversals use reviewed repository methods with fixed relationship
  allowlists — never unbounded variable-length paths.
- **Bounded.** Arguments are validated (`depth` 1..10, `limit` 1..200, ids ≤ 200
  non-whitespace characters), then clamped to `ENGGRAPH_MAX_DEPTH` /
  `ENGGRAPH_MAX_RESULTS`; the bounds used are echoed in the result, and
  `truncated` is reported when a bound was reached.
- **Authorized server-side.** Authorization is resolved before any graph read;
  a caller cannot read another organization, and a viewer's read is scoped like
  an admin's — the MCP surface grants nothing the API does not.
- **Endpoint-isolated.** Only `/mcp` is served. The REST API, health routes and
  the graph stores are not reachable through the MCP app.
- **DNS-rebinding protected.** The bound host must appear in `Host` (port
  included), otherwise `421`; a browser `Origin` that is not allowed gets `403`.
  A bind that clients reach through a published port or a proxy declares the
  address they present: `MCP_ALLOWED_HOSTS=127.0.0.1:*` and
  `MCP_ALLOWED_ORIGINS=http://127.0.0.1:8100`. A wider bind belongs behind a
  TLS-terminating proxy presenting exactly that host.
- **Credential-free.** No token, PAT, password, or store secret appears in a tool
  result. Every tool call is logged with tool, organization, user, depth, limit,
  result count and truncation — with the redaction filter applied, so no
  credential is logged.
- **Stores stay private.** Neo4j (7687) and Postgres (5432) are bound to
  localhost; an MCP client talks only to this server.

## Verifying it

```bash
cd backend
../.venv/Scripts/python -m pytest tests/test_mcp_cli.py tests/test_mcp_http.py tests/test_mcp_tools.py
```

These pin: the discovered surface is exactly the seven read-only tools, annotated
read-only, with no organization, credential, statement, or write parameter;
results are organization-scoped structured JSON with provenance, source URLs, bounds
and caveats; missing / invalid / revoked credentials and cross-organization reads
are refused in a way the client can read, and the session survives; the process
token is never inherited by an unauthenticated request; responses and access logs
carry no credential; unreachable stores are named (`graph_unavailable`,
`server_not_started`) instead of crashing; Host/Origin protection holds for the
localhost default, a wider explicit bind, and the container bind; only `/mcp` is
served; and the launch path refuses a broken configuration.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| exit code `2` at startup, `configuration problem: …` logged | `ENGGRAPH_SESSION_SECRET` missing, empty `NEO4J_PASSWORD`, placeholder `POSTGRES_DSN` (live mode) | fill `.env`; `docs/development.md` |
| `missing_token` | no `Authorization: Bearer` header (HTTP) / `ENGGRAPH_MCP_TOKEN` unset (stdio) | mint a session token, pass it per request; set the env token for stdio |
| `invalid_token` | token expired (`ENGGRAPH_SESSION_TTL_HOURS`), session revoked, wrong server | log in again; restart the stdio client so it picks up the new token |
| `graph_unavailable` | Neo4j unreachable at startup | start Neo4j, check `NEO4J_URI` / `NEO4J_PASSWORD` |
| `server_not_started` | tools called before the server's lifespan ran (embedding it without entering the app) | serve the app (`run_http` / `run_stdio`), don't call tools on a bare server |
| `401`/`403`/`421` from the HTTP endpoint | missing credential, unexpected `Origin`, `Host` that is not the bound host | send the bearer token; address the server by the host it is bound to, port included; declare `MCP_ALLOWED_HOSTS` |
| `406 Not Acceptable` | `Accept` does not allow `text/event-stream` on the stateful server | send `Accept: application/json, text/event-stream`, or use an MCP client |
| stdio: `ENGGRAPH_MCP_TOKEN is not set: stdio tool calls will be refused` | the client did not pass the env var | add it to the client's `env` block |
| empty results | bound reached, entity in another organization, catalog not configured | raise `depth` / `limit` within the ceilings; check the `caveats` list — absence is reported as absence of evidence |
| tools do not appear in the client | client does not support this transport, or its config file is malformed | check the client's MCP panel/logs; verify the command runs from the shell first |
