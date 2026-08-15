# Plane MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server for
[Plane](https://plane.so). Gives an AI agent tools to read and manage projects,
work items, cycles, modules, releases, customers and more.

Built on [FastMCP](https://github.com/jlowin/fastmcp) and the official
[`plane-sdk`](https://pypi.org/project/plane-sdk/).

- **29 tools**, one per Plane resource, covering 190 operations
- **Local or remote** — stdio, streamable HTTP, SSE
- **OAuth or API key** authentication

## Quick start

Get an API key from Plane: **Workspace Settings → API tokens**.

Add this to your MCP client's configuration:

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"],
      "env": {
        "PLANE_API_KEY": "<your-api-key>",
        "PLANE_WORKSPACE_SLUG": "<your-workspace-slug>"
      }
    }
  }
}
```

`uvx` needs no install step. Requires Python 3.10+.

For a self-hosted Plane, add `"PLANE_BASE_URL": "https://plane.example.com"`.

## Transports

### stdio — local

Runs as a subprocess of your MCP client. Configuration as shown above; needs
`PLANE_API_KEY` and `PLANE_WORKSPACE_SLUG`.

```bash
PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... uvx plane-mcp-server stdio
```

### HTTP with OAuth — hosted

`https://mcp.plane.so/http/mcp`

The OAuth flow is handled on connect; no credentials in your config. For clients
without native remote MCP support, bridge with `mcp-remote`:

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["mcp-remote@latest", "https://mcp.plane.so/http/mcp"]
    }
  }
}
```

Requires Node.js 22+.

### HTTP with a personal access token — hosted

`https://mcp.plane.so/http/api-key/mcp`

| Header | Value |
|---|---|
| `Authorization` | `Bearer <PAT>` |
| `X-Workspace-slug` | `<workspace-slug>` |

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["mcp-remote@latest", "https://mcp.plane.so/http/api-key/mcp"],
      "headers": {
        "Authorization": "Bearer <PAT>",
        "X-Workspace-slug": "<workspace-slug>"
      }
    }
  }
}
```

### SSE — deprecated

`https://mcp.plane.so/sse` is maintained for backward compatibility only. Use an
HTTP transport instead.

## Tools

The server advertises 29 tools, one per resource. Each takes an `action`
parameter that selects the operation:

```python
workitem(action="create", project_id=..., name="Fix login")
workitem(action="list", project_id=..., pql='state__group = "started"')
cycle(action="archive", project_id=..., cycle_id=...)
```

Every tool's description lists its actions with their required and optional
parameters, so the catalogue is self-documenting at call time.

**→ [Full tool and action reference](plane_mcp/tools/README.md)**

### Querying work items

List, count and search accept **PQL**, Plane's query language:

```python
workitem(action="list", project_id=..., pql='state__group = "started" AND priority = "urgent"')
workitem(action="count", pql='assignees__id = "<member id>"', group_by="state_id")
```

Call `get_pql_reference` for the full syntax, operators and worked examples.

### Upgrading from the per-operation tools

Earlier releases exposed one tool per API operation. **Existing integrations keep
working**: 169 of those 177 names still resolve to the consolidated tool, so a
saved prompt or script calling `create_work_item` or `list_cycles` needs no
change. They are no longer advertised, and they keep the parameter names they
shipped with (`work_item_id`, not `workitem_id`).

Seven names chose between two operations with a parameter
(`manage_project_archive(archive=False)`), which one tool-and-action pair cannot
reproduce; calling one tells you its replacement. `get_pql_reference` is
unchanged.

## Configuration

### Authentication

| Variable | Required for | Purpose |
|---|---|---|
| `PLANE_API_KEY` | stdio | API key |
| `PLANE_WORKSPACE_SLUG` | stdio | Target workspace |
| `PLANE_BASE_URL` | optional | Plane API URL (default `https://api.plane.so`) |

The remote transports carry credentials in the connection — the OAuth flow or the
PAT headers — and need none of these.

Self-hosting the server itself:

| Variable | Purpose |
|---|---|
| `PLANE_INTERNAL_BASE_URL` | Internal URL for server-to-server calls, preferred over `PLANE_BASE_URL` |
| `REDIS_HOST` / `REDIS_PORT` | OAuth token storage; falls back to in-memory |
| `PLANE_OAUTH_PROVIDER_*` | OAuth client credentials and base URL |
| `MCP_PATH_PREFIX` | Path prefix for the HTTP routes, when mounted behind a proxy — `/plane` serves `/plane/http/mcp` |

### OAuth redirect URIs

The OAuth transports validate each client's redirect URI against an allowlist.
Common clients (Cursor, VS Code, Claude.ai, ChatGPT connectors, localhost) are
allowed by default.

To onboard a new client without a release, append patterns:

```bash
export PLANE_OAUTH_ALLOWED_REDIRECT_URIS="https://newclient.com/cb,https://other.app/oauth/*"
```

`*` matches any port, path segment or subdomain. Keep the host pinned and
wildcard only the port or path.

### Logging

Structured JSON. Each tool call logs its name, duration, status and — when
available — an opaque user id and the workspace slug.

```bash
export LOG_USER_INFO=true    # also log the display name (PII);
export LOG_PAYLOADS=false    # keep request payloads out of logs; default true
```

Only the OAuth and PAT transports carry a display name; stdio is unaffected.

## Development

```bash
git clone https://github.com/makeplane/plane-mcp-server
cd plane-mcp-server
uv pip install -e ".[dev]"
```

Run the server against a workspace:

```bash
PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... python -m plane_mcp stdio
python -m plane_mcp http            # port 8211
```

Tests, format, lint:

```bash
pytest                              # no network or credentials needed
ruff format plane_mcp/ tests/       # line length 120
ruff check plane_mcp/ tests/        # rules E, F, I, UP, B
```

The suite runs fully offline — every action of every resource is
executed against a stand-in that binds each call against the genuine `plane-sdk`
signature. See [`plane_mcp/tools/README.md`](plane_mcp/tools/README.md#tests).

Live integration tests are skipped unless you point them at a running server:

```bash
export PLANE_TEST_API_KEY=... PLANE_TEST_WORKSPACE_SLUG=...
export PLANE_TEST_MCP_URL=http://localhost:8211    # optional; this is the default
pytest tests/test_integration.py -v
```

They write real data to that workspace.

### Repository layout

| Path | Contents |
|---|---|
| `plane_mcp/__main__.py` | entry point; picks the transport from `argv[1]` |
| `plane_mcp/server.py` | one factory per transport |
| `plane_mcp/client.py` | resolves credentials into a `plane-sdk` client |
| `plane_mcp/auth/` | OAuth provider and header auth |
| `plane_mcp/tools/` | the tool surface: one module per Plane resource |
| `plane_mcp/toolkit/` | shared building blocks for the tool surface |
| `plane_mcp/pql_reference.py` | PQL syntax reference served to models |

## Contributing

Pull requests welcome. Please run `pytest` and `ruff check` before submitting; new
tools should come with the invariants described in
[`plane_mcp/tools/README.md`](plane_mcp/tools/README.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Migrating from the Node.js server

`@makeplane/plane-mcp-server` (Node.js) is deprecated and unmaintained. This
Python implementation replaces it.

| Node.js | Python |
|---|---|
| `PLANE_API_KEY` | `PLANE_API_KEY` |
| `PLANE_API_HOST_URL` | `PLANE_BASE_URL` |
| `PLANE_WORKSPACE_SLUG` | `PLANE_WORKSPACE_SLUG` |

Replace the `command` and `args` with the stdio configuration in
[Quick start](#quick-start).

## License

MIT — see [LICENSE](LICENSE).
