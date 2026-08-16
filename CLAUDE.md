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

Three factory functions (`get_oauth_mcp`, `get_header_mcp`, `get_stdio_mcp`) each create a `FastMCP` instance and configure the appropriate auth provider, then hand it to `_configured()` for the middleware stack and tools — one place, so a transport cannot be left a middleware behind. OAuth/HTTP modes use Redis for token storage (falls back to in-memory).

### Middleware (`middleware.py`)

Ordered as registered; the earlier one wraps the later:

| Middleware | Does |
|---|---|
| `PlaneLoggingMiddleware` | structured logging, plus the tool name |
| `CoerceArguments` | repairs arguments a client encoded as strings, before validation (`coercion.py`) |
| `ValidateActionArguments` | refuses arguments the chosen action does not accept, from the `ACTIONS` declaration |

A dispatch tool's name is not its operation, so `PlaneLoggingMiddleware` adds `resource` and `action` to every `tools/call` record — start, success and error alike, where previously only success and error carried anything. For a retired name both are resolved through the alias table, since it carries no `action` of its own.

| field | means |
|---|---|
| `tool` | the name the caller used — **unchanged**, so existing dashboards keep counting the same thing |
| `resource` | the resource tool that ran (`workitem`); equals `tool` for a current call |
| `action` | the operation (`count`), absent only for a tool that has no actions |

`resource` + `action` names one operation however it was reached, and `tool != resource` is exactly the set of calls still arriving on a retired name. `legacy.py` also keeps its per-resolution log line, which predates these fields.

Coercion runs before validation so an argument is judged by the value it repairs to. `ValidateActionArguments` closes a gap a per-tool schema cannot: every action's parameters share one schema, so an argument meant for another action validated cleanly and was then dropped, and the call answered a different question than the one asked. Only arguments carrying a value are judged, and retired names are exempt — they arrive with no `action` and under their own parameter spelling.

### Client Context (`client.py`)

`get_plane_client_context()` returns a `PlaneClientContext(client, workspace_slug)` namedtuple. It resolves credentials from the MCP request context (OAuth token or header API key) or from environment variables (stdio mode). Prefers `PLANE_INTERNAL_BASE_URL` for server-to-server calls.

### Authentication (`auth/`)

- `PlaneOAuthProvider` — Full OAuth flow with token verification against the Plane API.
- `PlaneHeaderAuthProvider` — Simple header-based auth using `x-api-key` and `x-workspace-slug` headers.

### Tools (`tools/`)

One action-dispatch tool per Plane resource: **30 tools, 204 actions, ~67k chars advertised**. `tools/__init__.py` re-exports `register_tools`, so `server.py` and `__main__.py` see a single entry point.

One module per resource, each exporting `NAME`, `ACTIONS`, `LEGACY` and `register(mcp)`. `ACTIONS` is the single source of truth: the tool description and its `ToolAnnotations` are generated from it, and the conformance suite asserts they agree with the function signature. See `tools/README.md` for the full convention.

`tools/` contains resource modules plus `registry.py` (the `RESOURCES` tuple and alias tables) and `legacy.py` (retired-name resolution). Shared helpers live in `plane_mcp/toolkit/`, not here — see below.

Where a resource exists at both project and workspace scope, it resolves that once in a local `_scope_of` rather than through a shared abstraction: the two resources that need it need different shapes (`workitem_type` is a two-way split, `workitem_property` three-way plus a method-name suffix).

`RESOURCES` is an explicit tuple, not a directory scan. Its order is the advertised order and therefore a wire-format guarantee: tool definitions head a client's prompt cache, so reordering invalidates live conversations. Append; never re-sort. `test_resource_order_is_pinned` holds it to a literal list.

**Retired names.** Before consolidation this server exposed 177 tools, one per operation. 169 of those names still resolve, via a `Transform` mapping each to its `(tool, action)` pair with `action` hidden, and keeping the parameter spelling they shipped with (`work_item_id`, not `workitem_id`). The transforms implement `list_tools`/`get_tool` only — execution keeps the full schema, so tool results are unchanged, and nothing is advertised so the listing is unaffected. Seven encoded their action in a parameter and are declared in `LEGACY_UNMAPPED` with a replacement. `tests/tools/_retired_names.py` is the frozen record of all 177.

Tools return Pydantic models from `plane-sdk` and use Python 3.10+ union syntax (`str | None`).

### Toolkit (`toolkit/`)

Shared building blocks for the tool surface, split by *when* they act:

| Module | Acts at | Provides |
|---|---|---|
| `spec.py` | declaration | `Action`, `build_description`, `build_annotations` |
| `runtime.py` | call | `missing`, `needs`, `require`, `one_of`, `opt`, `coerce_list`, `page_params`, `as_params`, `ids_of` |
| `paging.py` | response | `envelope`, `dump_results`, `pql_failure`, `workitem_page` |
| `governance.py` | policy | `workspace_owns_resource`, `GOVERNED_BY`, `workspace_owns`, `migration_in_progress`, `plan_gated` |
| `transforms.py` | listing | `StripOutputSchemas` |

Governance has two questions, and both matter. `workspace_owns_resource` reads the workspace flag that governs a resource — used *before* a write, to pick the scope. `workspace_owns` reads the refusal — used *after*, because the flag is cached and the lockout outlives it being toggled off. There is no single flag: work item types carry their own (`is_work_item_types_enabled`, public `work_item_types`), while states, labels, workflows, templates and automations share `workspace_governance_status` (public `states_owned_by_workspace`). `GOVERNED_BY` maps resource to flag so a newly governed resource is one row.

Names are re-exported from `plane_mcp/toolkit/__init__.py`, so a resource module needs one import: `from plane_mcp.toolkit import Action, build_description, missing, opt`.

These sit outside `tools/` deliberately. They were previously `_`-prefixed modules inside the resource package, where the underscore was the module-discovery filter rather than a privacy marker — which made helper filenames load-bearing and made the most widely imported module in the package look private. Nothing here knows which catalogue is calling it.

Anything that encodes the catalogue's history — `LegacyNames`, the `RESOURCES` tuple — belongs under `tools/`, not here.

### Testing

`tests/tools/` covers the surface with no network and no credentials: surface-wide invariants, plus every action of every resource executed against `SpyClient`, a stand-in that binds each call against the genuine SDK signature and type-checks its arguments.

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
| `LOG_PAYLOADS` | all (optional, default: true) | Log request payloads.|
