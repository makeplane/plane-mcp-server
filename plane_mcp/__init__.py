"""Plane MCP Server - A Model Context Protocol server for Plane integration."""

import os
from contextlib import asynccontextmanager
from fastmcp import FastMCP

_original_fastmcp_init = FastMCP.__init__

def _patched_fastmcp_init(self, *args, **kwargs):
    if "tasks" not in kwargs:
        kwargs["tasks"] = os.getenv("PLANE_ALLOW_MEMORY_TASKS", "false").lower() == "true"
    _original_fastmcp_init(self, *args, **kwargs)
    self.__mcp_patched_tasks_enabled = kwargs.get("tasks", False)

FastMCP.__init__ = _patched_fastmcp_init

_original_docket_lifespan = FastMCP._docket_lifespan

@asynccontextmanager
async def _patched_docket_lifespan(self):
    tasks_enabled = getattr(self, "__mcp_patched_tasks_enabled", True)
    if not tasks_enabled:
        try:
            yield
        finally:
            pass
        return
    async with _original_docket_lifespan(self):
        yield

FastMCP._docket_lifespan = _patched_docket_lifespan
