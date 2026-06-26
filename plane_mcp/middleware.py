"""Custom FastMCP middleware for the Plane MCP Server."""

from __future__ import annotations

from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware


class PlaneLoggingMiddleware(StructuredLoggingMiddleware):
    """StructuredLoggingMiddleware that also records the tool name."""

    def _with_tool_name(self, context: MiddlewareContext, message: dict) -> dict:
        if context.method == "tools/call":
            message["tool"] = getattr(context.message, "name", "unknown")
        return message

    def _create_after_message(self, context: MiddlewareContext, start_time: float) -> dict:
        return self._with_tool_name(context, super()._create_after_message(context, start_time))

    def _create_error_message(self, context: MiddlewareContext, start_time: float, error: Exception) -> dict:
        return self._with_tool_name(context, super()._create_error_message(context, start_time, error))
