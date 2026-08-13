"""Custom FastMCP middleware for the Plane MCP Server."""

from __future__ import annotations

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.utilities.logging import get_logger

from plane_mcp.coercion import coerce_arguments

logger = get_logger(__name__)


class CoerceArguments(Middleware):
    """Repair arguments a client encoded as strings, before they are validated."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        arguments = getattr(context.message, "arguments", None)
        if not arguments:
            return await call_next(context)

        schema = await self._schema(context)
        if schema is None:
            return await call_next(context)

        try:
            repaired, touched = coerce_arguments(arguments, schema)
        except Exception:  # noqa: BLE001 - never break a call that would have worked
            logger.exception("Plane MCP: argument coercion failed for %s; passing through", context.message.name)
            return await call_next(context)

        if touched:
            logger.warning(
                "Plane MCP: %s sent %s as string(s); decoded before validation",
                context.message.name,
                ", ".join(sorted(touched)),
            )
            context.message.arguments = repaired
        return await call_next(context)

    @staticmethod
    async def _schema(context: MiddlewareContext) -> dict | None:
        """The called tool's input schema, or None if it cannot be resolved.

        A retired tool name resolves through the alias transform here, so it is
        repaired against the schema the caller actually sees.
        """
        server = getattr(context.fastmcp_context, "fastmcp", None)
        if server is None:
            return None
        try:
            tool = await server.get_tool(context.message.name)
        except Exception:  # noqa: BLE001 - an unknown tool is call_next's error to raise
            return None
        return getattr(tool, "parameters", None)


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
