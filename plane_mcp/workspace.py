"""Per-call workspace selection.

Upstream binds a connection to one workspace (environment or ``x-workspace-slug``
header). This module lets a caller name the workspace on each tool call instead,
so one connection can serve every workspace its token may access.

The ``SelectWorkspace`` middleware removes the ``workspace`` argument before the
tool sees it and parks the slug in a ContextVar that ``client._workspace_slug``
reads first. Tool modules stay untouched.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

WORKSPACE_PARAM = "workspace"

_override: ContextVar[str | None] = ContextVar("plane_workspace_override", default=None)


def workspace_override() -> str | None:
    """The workspace slug named on the current tool call, if any."""
    return _override.get()


def _with_workspace_property(schema: dict[str, Any]) -> dict[str, Any]:
    properties = {
        **schema.get("properties", {}),
        WORKSPACE_PARAM: {
            "type": "string",
            "description": (
                "Workspace slug (first path segment of the Plane URL, e.g. 'tf'). "
                "Optional when the connection already names a workspace."
            ),
        },
    }
    return {**schema, "properties": properties}


class SelectWorkspace(Middleware):
    """Accept an optional ``workspace`` argument on every tool."""

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        return [t.model_copy(update={"parameters": _with_workspace_property(t.parameters)}) for t in tools]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        arguments = getattr(context.message, "arguments", None)
        if not isinstance(arguments, dict) or WORKSPACE_PARAM not in arguments:
            return await call_next(context)

        remaining = {k: v for k, v in arguments.items() if k != WORKSPACE_PARAM}
        slug = str(arguments[WORKSPACE_PARAM] or "").strip() or None
        context.message.arguments = remaining
        reset = _override.set(slug)
        try:
            return await call_next(context)
        finally:
            _override.reset(reset)
