"""Main entry point for the Plane MCP Server."""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from enum import Enum

import uvicorn
from fastmcp.server.dependencies import get_access_token
from starlette.applications import Starlette

from plane_mcp.server import get_stdio_mcp
from plane_mcp.surfaces import build_mcp_app

LOG_USER_INFO: bool = os.getenv("LOG_USER_INFO", "").lower() == "true"


class UserContextFilter(logging.Filter):
    """Logging filter that enriches log records with Plane user and workspace context."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Populate user_id, display_name, and workspace_slug on the log record."""
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
            record.user_context_enrichment_error = type(exc).__name__
        record.user_id = user_id
        record.display_name = display_name
        record.workspace_slug = workspace_slug or os.getenv("PLANE_WORKSPACE_SLUG") or None
        return True


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for FastMCP and Uvicorn logs."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the log record as a JSON string."""
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
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


def configure_json_logging() -> None:
    """Configure the root FastMCP logger with structured JSON formatting and user context filtering."""
    fastmcp_logger = logging.getLogger("fastmcp")
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
    """Supported transport modes for launching the Plane MCP Server."""

    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


def create_app() -> Starlette:
    """Create and configure the multi-surface Starlette application."""
    prefix = os.getenv("MCP_PATH_PREFIX") or ""
    return build_mcp_app(prefix=prefix)


def get_app() -> Starlette:
    """Return the cached Starlette application instance, creating it if needed."""
    global _cached_app
    if _cached_app is None:
        _cached_app = create_app()
    return _cached_app


_cached_app = None


def main() -> None:
    """Parse CLI arguments and launch the Plane MCP Server in stdio or HTTP mode."""
    server_mode = ServerMode.STDIO
    if len(sys.argv) > 1:
        server_mode = ServerMode(sys.argv[1])

    surface = os.getenv("PLANE_MCP_SURFACE", "admin").lower()

    if server_mode == ServerMode.STDIO:
        if not os.getenv("PLANE_API_KEY"):
            raise ValueError("PLANE_API_KEY is not set")
        if not os.getenv("PLANE_WORKSPACE_SLUG"):
            raise ValueError("PLANE_WORKSPACE_SLUG is not set")

        get_stdio_mcp(surface=surface).run()
        return

    if server_mode == ServerMode.HTTP:
        for uv_logger_name in ("uvicorn", "uvicorn.error"):
            uv_logger = logging.getLogger(uv_logger_name)
            for h in uv_logger.handlers[:]:
                uv_logger.removeHandler(h)
            uv_handler = logging.StreamHandler(sys.stderr)
            uv_handler.setFormatter(JSONFormatter())
            uv_handler.addFilter(UserContextFilter())
            uv_logger.addHandler(uv_handler)

        logger.info("Starting Multi-Surface Plane MCP Server (Admin, Agent, Docs) on port 8211")
        uvicorn.run(
            get_app(),
            host="0.0.0.0",
            port=8211,
            log_level="info",
            access_log=False,
        )


if __name__ == "__main__":
    main()
