"""Main entry point for the Plane MCP Server."""

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum

import uvicorn
from fastmcp.server.dependencies import get_access_token
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from plane_mcp.server import get_header_mcp, get_oauth_mcp, get_stdio_mcp

LOG_USER_INFO: bool = os.getenv("LOG_USER_INFO", "").lower() == "true"


class UserContextFilter(logging.Filter):
    """Attach authenticated user/workspace context to every log record.

    Pulls the current request's access token via FastMCP's dependency, which
    returns None (never raises) outside a request context — so startup logs fall
    back to environment config and otherwise carry no user info.

    Always logs the opaque user id (sub claim) and the workspace slug; neither is
    PII. The display name IS PII and is only included when LOG_USER_INFO=true.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        user_id = None
        display_name = None
        workspace_slug = None
        try:
            token = get_access_token()
            if token:
                user_id = token.claims.get("sub")
                workspace_slug = token.claims.get("workspace_slug")
                if LOG_USER_INFO:
                    display_name = token.claims.get("display_name")
        except Exception as exc:
            # Never let logging enrichment break a request, but leave a signal.
            record.user_context_enrichment_error = type(exc).__name__
        record.user_id = user_id
        record.display_name = display_name
        # stdio mode has no token; fall back to the configured workspace.
        record.workspace_slug = workspace_slug or os.getenv("PLANE_WORKSPACE_SLUG") or None
        return True


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging (Datadog, ELK, etc.)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        # The logging middleware emits a JSON object as its log message. Promote
        # those keys to top-level fields (event, method, tool, duration_ms, ...)
        raw_message = record.getMessage()
        try:
            parsed = json.loads(raw_message)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            log_entry.update(parsed)
        else:
            log_entry["message"] = raw_message
        user_id = getattr(record, "user_id", None)
        if user_id:
            log_entry["user_id"] = user_id
        workspace_slug = getattr(record, "workspace_slug", None)
        if workspace_slug:
            log_entry["workspace_slug"] = workspace_slug
        display_name = getattr(record, "display_name", None)
        if display_name:
            log_entry["display_name"] = display_name
        err = getattr(record, "user_context_enrichment_error", None)
        if err:
            log_entry["user_context_enrichment_error"] = err
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = str(record.exc_info[1])
            log_entry["error_type"] = type(record.exc_info[1]).__name__
        return json.dumps(log_entry)


def configure_json_logging():
    """Replace FastMCP's Rich handlers with a JSON formatter on the fastmcp logger."""
    fastmcp_logger = logging.getLogger("fastmcp")

    # Remove all existing handlers (Rich)
    for handler in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(UserContextFilter())
    fastmcp_logger.addHandler(handler)
    fastmcp_logger.setLevel(logging.INFO)
    fastmcp_logger.propagate = False


configure_json_logging()

logger = logging.getLogger("fastmcp.plane_mcp")


class ServerMode(Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


async def health(request: Request) -> JSONResponse:
    """Liveness and readiness probe endpoint."""
    return JSONResponse({"status": "ok"})


async def liveness(request: Request) -> JSONResponse:
    """Dedicated liveness probe endpoint."""
    return JSONResponse({"status": "ok"})


async def readiness(request: Request) -> JSONResponse:
    """Dedicated readiness probe endpoint."""
    return JSONResponse({"status": "ok"})


@asynccontextmanager
async def combined_lifespan(oauth_app, header_app, sse_app):
    """Combine lifespans from both OAuth and Header MCP apps."""
    # Start both lifespans
    async with oauth_app.lifespan(oauth_app):
        async with header_app.lifespan(header_app):
            async with sse_app.lifespan(sse_app):
                yield


def main() -> None:
    """Run the MCP server."""
    server_mode = ServerMode.STDIO
    if len(sys.argv) > 1:
        server_mode = ServerMode(sys.argv[1])

    if server_mode == ServerMode.STDIO:
        # Validate API_KEY and PLANE_WORKSPACE_SLUG are set
        if not os.getenv("PLANE_API_KEY"):
            raise ValueError("PLANE_API_KEY is not set")
        if not os.getenv("PLANE_WORKSPACE_SLUG"):
            raise ValueError("PLANE_WORKSPACE_SLUG is not set")

        get_stdio_mcp().run()
        return

    if server_mode == ServerMode.HTTP:
        prefix = os.getenv("MCP_PATH_PREFIX") or ""

        header_app = get_header_mcp().http_app(stateless_http=True)

        # Only create OAuth MCP instances if PLANE_OAUTH_PROVIDER_CLIENT_ID is set
        oauth_mcp = None
        oauth_app = None
        oauth_well_known = []
        if os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID"):
            oauth_mcp = get_oauth_mcp(prefix + "/http")
            oauth_app = oauth_mcp.http_app(stateless_http=True)
            oauth_well_known = oauth_mcp.auth.get_well_known_routes(mcp_path="/mcp")

        # Only create SSE MCP instance if OAuth is configured
        sse_mcp = None
        sse_app = None
        sse_well_known = []
        if oauth_mcp:
            sse_mcp = get_oauth_mcp(prefix)
            sse_app = sse_mcp.http_app(transport="sse")
            sse_well_known = sse_mcp.auth.get_well_known_routes(mcp_path="/sse")

        # mcp_path is appended to the auth provider's base_url to form the
        # advertised resource URL. base_url already carries the prefix, so these
        # stay at /mcp and /sse to avoid double-prefixing.

        # Health/readiness routes — must come before MCP mounts so probes don't
        # hit authenticated endpoints and get 401/404.
        health_routes = [
            Route("/health", health),
            Route("/live", liveness),
            Route("/ready", readiness),
        ]

        # Build routes list dynamically based on what's configured
        routes = [
            # Well-known routes for Header HTTP
            Mount(prefix + "/http/api-key", app=header_app),
        ]
        if oauth_well_known:
            routes.extend(oauth_well_known)
        if sse_well_known:
            routes.extend(sse_well_known)
        if oauth_app:
            routes.append(Mount(prefix + "/http", app=oauth_app))
        if sse_app:
            routes.append(Mount(prefix or "/", app=sse_app))

        # Build lifespan based on what's running
        async def dynamic_lifespan(app):
            async with header_app.lifespan(header_app):
                if oauth_app:
                    async with oauth_app.lifespan(oauth_app):
                        if sse_app:
                            async with sse_app.lifespan(sse_app):
                                yield
                        else:
                            yield
                else:
                    yield

        app = Starlette(
            routes=health_routes + routes,
            lifespan=dynamic_lifespan,
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Configure uvicorn loggers to use JSON formatting too
        for uv_logger_name in ("uvicorn", "uvicorn.error"):
            uv_logger = logging.getLogger(uv_logger_name)
            for h in uv_logger.handlers[:]:
                uv_logger.removeHandler(h)
            uv_handler = logging.StreamHandler(sys.stderr)
            uv_handler.setFormatter(JSONFormatter())
            uv_handler.addFilter(UserContextFilter())
            uv_logger.addHandler(uv_handler)

        logger.info("Starting HTTP server at URLs: /mcp and /header/mcp")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8211,
            log_level="info",
            access_log=False,
        )
        return


if __name__ == "__main__":
    main()
