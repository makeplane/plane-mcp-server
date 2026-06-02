"""Main entry point for the Plane MCP Server."""

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from plane_mcp.server import get_header_mcp, get_oauth_mcp, get_stdio_mcp


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging (Datadog, ELK, etc.)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }
        return json.dumps(log_entry)


def configure_json_logging():
    """Replace FastMCP's Rich handlers with a JSON formatter on the fastmcp logger."""
    fastmcp_logger = logging.getLogger("fastmcp")

    # Remove all existing handlers (Rich)
    for handler in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    fastmcp_logger.addHandler(handler)
    fastmcp_logger.setLevel(logging.INFO)
    fastmcp_logger.propagate = False


configure_json_logging()

logger = logging.getLogger("fastmcp.plane_mcp")


class ServerMode(Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


def resolve_bind() -> tuple[str, int]:
    """Resolve the HTTP host/port to bind from the environment.

    ``MCP_HOST`` (default ``0.0.0.0``) and ``MCP_PORT`` (default ``8211``). Platforms
    such as Cloud Run inject ``$PORT``; when set it takes precedence over ``MCP_PORT``
    so the service always binds the platform-assigned port.
    """
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("MCP_PORT", "8211"))
    return host, port


async def healthz(request: Request) -> JSONResponse:
    """Liveness/readiness probe for container orchestration (Cloud Run, k8s)."""
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
        host, port = resolve_bind()
        prefix = os.getenv("MCP_PATH_PREFIX") or ""

        oauth_mcp = get_oauth_mcp(prefix + "/http")
        oauth_app = oauth_mcp.http_app(stateless_http=True)
        header_app = get_header_mcp().http_app(stateless_http=True)

        sse_mcp = get_oauth_mcp(prefix)
        sse_app = sse_mcp.http_app(transport="sse")

        # mcp_path is appended to the auth provider's base_url to form the
        # advertised resource URL. base_url already carries the prefix, so these
        # stay at /mcp and /sse to avoid double-prefixing.
        oauth_well_known = oauth_mcp.auth.get_well_known_routes(mcp_path="/mcp")
        sse_well_known = sse_mcp.auth.get_well_known_routes(mcp_path="/sse")

        app = Starlette(
            routes=[
                Route("/healthz", healthz),
                # Well-known routes for OAuth and Header HTTP
                *oauth_well_known,
                *sse_well_known,
                # Mount both MCP servers
                Mount(prefix + "/http/api-key", app=header_app),
                Mount(prefix + "/http", app=oauth_app),
                Mount(prefix or "/", app=sse_app),
            ],
            lifespan=lambda app: combined_lifespan(oauth_app, header_app, sse_app),
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
            uv_logger.addHandler(uv_handler)

        logger.info("Starting HTTP server on %s:%s", host, port)
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        return


if __name__ == "__main__":
    main()
