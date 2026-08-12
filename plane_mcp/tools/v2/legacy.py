"""Keep every pre-consolidation tool name callable without advertising it.

`list_tools` is untouched, so the catalogue stays at 29. `get_tool` resolves a
legacy name to its resource tool with the action pre-bound and hidden, so a
caller with `create_label` hardcoded in a script or prompt keeps working.

Removed together with the v1 surface in the next major release.
"""

from __future__ import annotations

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools.base import Tool
from fastmcp.tools.tool_transform import ArgTransform


class LegacyNames(Transform):
    def __init__(self, aliases: dict[str, tuple[str, str]]) -> None:
        self._aliases = aliases

    async def get_tool(self, name: str, call_next: GetToolNext, *, version=None) -> Tool | None:
        target = self._aliases.get(name)
        if target is None:
            return await call_next(name, version=version)

        tool_name, action = target
        parent = await call_next(tool_name, version=version)
        if parent is None:
            return None
        return Tool.from_tool(
            parent,
            name=name,
            transform_args={"action": ArgTransform(hide=True, default=action)},
        )
