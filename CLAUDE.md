# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Plane MCP Server is a Python-based Model Context Protocol (MCP) server that integrates with Plane, a project management platform. It provides AI agents with access to Plane's functionality through a standardized MCP interface using the FastMCP framework.

## Build/Test/Lint Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run integration tests (requires PLANE_TEST_* env vars)
pytest tests/ -m integration -v

# Format code
black plane_mcp/

# Lint code
ruff check plane_mcp/
```

## Environment Variables

**For stdio mode (required)**:
- `PLANE_API_KEY` - Plane API key
- `PLANE_WORKSPACE_SLUG` - Workspace slug
- `PLANE_BASE_URL` - API URL (default: `https://api.plane.so`)

**For OAuth mode**:
- `PLANE_OAUTH_PROVIDER_CLIENT_ID`, `PLANE_OAUTH_PROVIDER_CLIENT_SECRET`
- `PLANE_OAUTH_PROVIDER_BASE_URL` - Public URL for OAuth endpoints
- `REDIS_HOST`, `REDIS_PORT` - Optional, defaults to memory store

**For testing**:
- `PLANE_TEST_API_KEY`, `PLANE_TEST_WORKSPACE_SLUG`, `PLANE_TEST_BASE_URL`

**For logging**:
- `LOG_FORMAT` - `json` (default, for Datadog), `rich` (local dev), or `text` (plain)
- `LOG_LEVEL` - DEBUG, INFO, WARNING, ERROR (default: INFO)
- `DD_SERVICE` - Datadog service name (default: `plane-mcp-server`)
- `DD_ENV` - Datadog environment tag
- `DD_VERSION` - Datadog version tag

## Architecture

### Transport Modes
The server supports three transport mechanisms configured in `plane_mcp/__main__.py`:
1. **Stdio** - Local CLI usage with env-based auth
2. **HTTP (Streamable HTTP)** - Remote with header-based auth (`x-api-key`, `x-workspace-slug`)
3. **SSE** - Server-Sent Events for OAuth (legacy support)

### Key Files
- `plane_mcp/__main__.py` - Entry point, runs on port 8211 for HTTP
- `plane_mcp/server.py` - Three factory functions: `get_stdio_mcp()`, `get_oauth_mcp()`, `get_header_mcp()`
- `plane_mcp/client.py` - `get_plane_client_context()` returns authenticated PlaneClient + workspace
- `plane_mcp/logging.py` - Logging config (JSON/Rich/text modes)
- `plane_mcp/middleware.py` - HTTP structured logging middleware for Datadog
- `plane_mcp/auth/` - OAuth proxy (`plane_oauth_provider.py`) and header auth (`plane_header_auth_provider.py`)
- `plane_mcp/tools/` - 55+ MCP tools grouped by domain (projects, work_items, cycles, modules, etc.)

### Tool Registration
All tools registered in `plane_mcp/tools/__init__.py` via `register_tools(mcp)`. Tools span 17 categories including projects, work items, cycles, modules, initiatives, intake, labels, states, and more.

### Tool Development Pattern
```python
def register_project_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def list_projects(...) -> list[Project]:
        """Docstring with Args and Returns."""
        client, workspace_slug = get_plane_client_context()
        response = client.projects.list(...)
        return response.results
```

Key patterns:
- Use `@mcp.tool()` decorator
- Call `get_plane_client_context()` for authenticated client
- Use Pydantic models from `plane.models.*` for type safety
- Return typed data structures

## Code Quality

- **Black**: Line length 100, Python 3.10+
- **Ruff**: Rules E, F, I, UP, B; line length 100

## Docker

```bash
docker-compose up --build
# OAuth: /mcp, Header auth: /header/mcp
```
