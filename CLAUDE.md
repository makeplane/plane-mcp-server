# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Plane MCP Server — a Python-based Model Context Protocol server that exposes Plane's project management API as MCP tools. Built on FastMCP with the official `plane-sdk`. Supports three transport modes: stdio (local), HTTP (with OAuth or header auth), and SSE (legacy).

## Common Commands

```bash
# Install dependencies (uses uv)
uv pip install -e ".[dev]"

# Run the server locally (stdio mode)
PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... python -m plane_mcp stdio

# Run HTTP server
python -m plane_mcp http

# Run all tests
pytest

# Run a single test
pytest tests/test_integration.py::test_full_integration -v

# Run tests with env vars from file
export $(cat .env.test.local | xargs) && pytest tests/ -v

# Format code (line length: 120)
ruff format plane_mcp/

# Lint (rules: E, F, I, UP, B; line length: 120)
ruff check plane_mcp/
```

## Architecture

### Entry Point & Transport Modes

`plane_mcp/__main__.py` parses a positional arg (`stdio`, `http`, or `sse`) and launches the corresponding server:
- **stdio**: Requires `PLANE_API_KEY` + `PLANE_WORKSPACE_SLUG` env vars. Runs locally.
- **http**: Starts on port 8211 with two auth endpoints — OAuth (`/oauth/mcp`) and header-based PAT (`/http/api-key/mcp`).
- **sse**: Legacy OAuth-only SSE transport.

### Server Factories (`server.py`)

Three factory functions (`get_oauth_mcp`, `get_header_mcp`, `get_stdio_mcp`) each create a `FastMCP` instance, register all tools, and configure the appropriate auth provider. OAuth/HTTP modes use Redis for token storage (falls back to in-memory).

### Client Context (`client.py`)

`get_plane_client_context()` returns a `PlaneClientContext(client, workspace_slug)` namedtuple. It resolves credentials from the MCP request context (OAuth token or header API key) or from environment variables (stdio mode). Prefers `PLANE_INTERNAL_BASE_URL` for server-to-server calls.

### Authentication (`auth/`)

- `PlaneOAuthProvider` — Full OAuth flow with token verification against the Plane API.
- `PlaneHeaderAuthProvider` — Simple header-based auth using `x-api-key` and `x-workspace-slug` headers.

### Tools (`tools/`)

Two surfaces live side by side. `tools/__init__.py` picks one at registration time from `PLANE_MCP_TOOLS_VERSION`, so `server.py` and `__main__.py` never see the difference.

| Value | Surface | Tools | Advertised |
|---|---|---|---|
| `v2` (default) | `tools/v2/` — one action-dispatch tool per resource | 28 | ~55k chars |
| `v1` | `tools/v1/` — the original flat tools | 177 | ~500k chars |

`v1` is retained for one major release and logs a deprecation warning. Note that "tools v2" is this server's tool surface, not Plane's API v2 — they version independently.

#### Consolidated surface (`tools/v2/`)

One module per resource, each exporting `NAME`, `ACTIONS`, `LEGACY` and `register(mcp)`. `ACTIONS` is the single source of truth: the tool description and its `ToolAnnotations` are generated from it, and the conformance suite asserts they agree with the function signature. See `tools/v2/README.md` for the full convention and the rules that are easy to break.

`tools/v2/` contains resource modules plus `registry.py` (the `RESOURCES` tuple and alias tables) and `legacy.py` (v1 name resolution). Shared helpers live in `plane_mcp/toolkit/`, not here — see below.

Where a resource exists at both project and workspace scope, it resolves that once in a local `_scope_of` rather than through a shared abstraction: the two resources that need it need different shapes (`work_item_type` is a two-way split, `work_item_property` three-way plus a method-name suffix).

`RESOURCES` is an explicit tuple, not a directory scan. Its order is the advertised order and therefore a wire-format guarantee: tool definitions head a client's prompt cache, so reordering invalidates live conversations. Append; never re-sort. `test_resource_order_is_pinned` holds it to a literal list.

169 of the 177 v1 tool names still resolve, via a `Transform` that maps each to its `(tool, action)` pair with `action` hidden. The transforms implement `list_tools`/`get_tool` only — execution keeps the full schema, so tool results are unchanged.

**Tool pattern (v1):**
```python
def register_*_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def tool_name(param: str, optional_param: str | None = None) -> SomePlaneModel:
        """Docstring with Args and Returns sections."""
        client, workspace_slug = get_plane_client_context()
        return client.endpoint.operation(workspace_slug=workspace_slug, ...)
```

Tools return Pydantic models from `plane-sdk` and use Python 3.10+ union syntax (`str | None`).

### Toolkit (`toolkit/`)

Shared building blocks for tool surfaces, split by *when* they act:

| Module | Acts at | Provides |
|---|---|---|
| `spec.py` | declaration | `Action`, `build_description`, `build_annotations` |
| `runtime.py` | call | `missing`, `needs`, `require`, `one_of`, `opt`, `coerce_list`, `page_params`, `as_params`, `ids_of` |
| `paging.py` | response | `envelope`, `dump_results`, `pql_failure`, `work_item_page` |
| `transforms.py` | listing | `StripOutputSchemas` |

Names are re-exported from `plane_mcp/toolkit/__init__.py`, so a resource module needs one import: `from plane_mcp.toolkit import Action, build_description, missing, opt`.

These sit outside `tools/` deliberately. They were previously `_`-prefixed modules inside `tools/v2/`, where the underscore was the module-discovery filter rather than a privacy marker — which made helper filenames load-bearing and made the most widely imported module in the package look private. Nothing here knows which catalogue is calling it, and `tools/v2/` is scheduled to be renamed when v1 is dropped.

Anything that encodes one catalogue's history — `LegacyNames`, the `RESOURCES` tuple — belongs under `tools/`, not here.

### Testing

`tests/tools/v2/` covers the consolidated surface with no network and no credentials: surface-wide invariants, plus every action of every resource executed against `SpyClient`, a stand-in that binds each call against the genuine SDK signature and type-checks its arguments.

Integration tests in `tests/test_integration.py` use `FastMCP.Client` with `StreamableHttpTransport`. Tests run against a live Plane instance — configure via `.env.test` (copy to `.env.test.local` with real values).

## Key Environment Variables

| Variable | Required For | Purpose |
|---|---|---|
| `PLANE_API_KEY` | stdio | API key for authentication |
| `PLANE_WORKSPACE_SLUG` | stdio | Target workspace |
| `PLANE_BASE_URL` | all (default: https://api.plane.so) | Plane API URL |
| `PLANE_INTERNAL_BASE_URL` | http/sse (optional) | Internal URL for server-to-server calls |
| `REDIS_HOST` / `REDIS_PORT` | http/sse (optional) | Token storage (falls back to in-memory) |
| `PLANE_OAUTH_PROVIDER_*` | http/sse OAuth | OAuth client credentials and base URL |
| `PLANE_OAUTH_ALLOWED_REDIRECT_URIS` | http/sse OAuth (optional) | Comma-separated redirect URI patterns appended to the built-in allowlist (onboard clients without a release) |
| `LOG_USER_INFO` | all (optional, default: false) | When `true`, include user info (PII such as display name) in logs alongside the opaque user id |
| `PLANE_MCP_TOOLS_VERSION` | all (optional, default: v2) | `v2` for the 28-tool consolidated surface, `v1` for the legacy 177 flat tools |
