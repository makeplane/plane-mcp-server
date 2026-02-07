"""Logging configuration with JSON (Datadog) and Rich (local dev) support."""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Literal

LogFormat = Literal["json", "rich", "text"]


def _get_log_format() -> LogFormat:
    """Get the configured log format."""
    fmt = os.getenv("LOG_FORMAT", "json").lower()
    if fmt in ("json", "rich", "text"):
        return fmt  # type: ignore[return-value]
    return "json"


def _is_rich_enabled() -> bool:
    """Check if Rich logging is enabled."""
    return _get_log_format() == "rich"


# Disable FastMCP's Rich logging UNLESS we want Rich output
# This must happen before any fastmcp imports to prevent RichHandler setup
if not _is_rich_enabled():
    os.environ.setdefault("FASTMCP_LOG_ENABLED", "false")

from pythonjsonlogger import jsonlogger  # noqa: E402


class DatadogJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter optimized for Datadog log ingestion."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # Datadog standard fields
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["service"] = os.getenv("DD_SERVICE", "plane-mcp-server")

        # Add source location for debugging
        log_record["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }

        # Add environment if set
        env = os.getenv("DD_ENV")
        if env:
            log_record["env"] = env

        # Add version if set
        version = os.getenv("DD_VERSION")
        if version:
            log_record["version"] = version

        # Include exception info if present
        if record.exc_info:
            log_record["error"] = {
                "kind": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
            }


def get_log_level() -> str:
    """Get the configured log level."""
    return os.getenv("LOG_LEVEL", "INFO")


def get_uvicorn_log_config() -> dict | None:
    """Get uvicorn logging configuration.

    Returns:
        Dictionary compatible with uvicorn's log_config parameter,
        or None for Rich/text mode (uses uvicorn defaults).
    """
    log_format = _get_log_format()

    # For Rich or text mode, return None to use uvicorn's default config
    if log_format != "json":
        return None

    log_level = get_log_level()

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "plane_mcp.logging.DatadogJsonFormatter",
                "fmt": "%(timestamp)s %(level)s %(name)s %(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "json",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": log_level, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": log_level, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": log_level, "propagate": False},
        },
    }


def configure_logging(level: str | None = None) -> None:
    """Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to LOG_LEVEL env var or INFO.

    Environment variables:
        LOG_FORMAT: "json" (default), "rich" (local dev), or "text" (plain)
        LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
    """
    log_level = level or get_log_level()
    log_format = _get_log_format()

    # For Rich mode, use FastMCP's Rich logging configuration
    if log_format == "rich":
        from fastmcp.utilities.logging import configure_logging as fastmcp_configure_logging

        fastmcp_configure_logging(level=log_level)  # type: ignore[arg-type]
        return

    # Create appropriate formatter for JSON or text
    if log_format == "json":
        formatter = DatadogJsonFormatter(
            fmt="%(timestamp)s %(level)s %(name)s %(message)s",
            json_ensure_ascii=False,
        )
    else:  # text
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Create handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Override FastMCP's logging by configuring its loggers
    fastmcp_loggers = [
        "fastmcp",
        "fastmcp.server",
        "fastmcp.server.auth",
        "fastmcp.utilities",
        "mcp",
        "mcp.server",
    ]
    for logger_name in fastmcp_loggers:
        lib_logger = logging.getLogger(logger_name)
        lib_logger.handlers.clear()
        lib_logger.addHandler(handler)
        lib_logger.propagate = False
        lib_logger.setLevel(log_level)

    # Pre-configure uvicorn loggers (will be reinforced by log_config)
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.propagate = False
