# Plane MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server for
[Plane](https://plane.so). Gives an AI agent tools to read and manage projects,
work items, cycles, modules, releases, customers and more.

Built on [FastMCP](https://github.com/jlowin/fastmcp) and the official
[`plane-sdk`](https://pypi.org/project/plane-sdk/).

- **28 tools**, one per Plane resource, covering 183 operations
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

The server advertises 28 tools, one per resource. Each takes an `action`
parameter that selects the operation:

```python
workitem(action="create", project_id=..., name="Fix login")
workitem(action="list", project_id=..., pql='state__group = "started"')
cycle(action="archive", project_id=..., cycle_id=...)
```

Every tool's description lists its actions with their required and optional
parameters, so the catalogue is self-documenting at call time.

**→ [Full tool and action reference](plane_mcp/tools/v2/README.md)**

### Querying work items

List, count and search accept **PQL**, Plane's query language:

```python
workitem(action="list", project_id=..., pql='state__group = "started" AND priority = "urgent"')
workitem(action="count", pql='assignees__id = "<member id>"', group_by="state_id")
```

Call `get_pql_reference` for the full syntax, operators and worked examples.

### Tool surface version

`PLANE_MCP_TOOLS_VERSION` selects which catalogue the server advertises.

| Value | Surface | Status |
|---|---|---|
| unset or `v2` | 28 consolidated tools | Default |
| `v1` | 177 flat tools, one per operation | Deprecated |

```bash
export PLANE_MCP_TOOLS_VERSION=v1   # opt in to the flat surface
```

`v1` is kept for one major release to ease migration and logs a warning on
start-up. It is documented in
**[`plane_mcp/tools/v1/README.md`](plane_mcp/tools/v1/README.md)**.

Most integrations need no change to move between them. Of the 177 flat tool
names, 169 are still accepted on the default surface — no longer advertised, but
calling `create_work_item` or `list_cycles` resolves to the consolidated tool.
`get_pql_reference` keeps its name on both. The remaining seven chose between two
operations with a parameter (`manage_project_archive(archive=False)`), which an
alias cannot reproduce; calling one names its replacement.

This setting versions **this server's tool surface** and is unrelated to Plane's
API versions.

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
export LOG_USER_INFO=true    # also log the display name (PII); default false
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

The default surface has a full offline suite — every action of every resource is
executed against a stand-in that binds each call against the genuine `plane-sdk`
signature. See [`plane_mcp/tools/v2/README.md`](plane_mcp/tools/v2/README.md#tests).

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
| `plane_mcp/tools/` | the two tool surfaces, selected by `PLANE_MCP_TOOLS_VERSION` |
| `plane_mcp/toolkit/` | shared building blocks for tool surfaces |
| `plane_mcp/pql_reference.py` | PQL syntax reference served to models |

## Contributing

Pull requests welcome. Please run `pytest` and `ruff check` before submitting; new
tools on the default surface should come with the invariants described in
[`plane_mcp/tools/v2/README.md`](plane_mcp/tools/v2/README.md).

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
