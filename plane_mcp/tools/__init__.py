"""The MCP tool surface: one action-dispatch tool per Plane resource.

`register_tools(mcp)` attaches all 28 tools plus the transforms that shape the
advertised listing. See `plane_mcp/tools/v2/README.md` for the convention a
resource module follows.
"""

from __future__ import annotations

from plane_mcp.tools.v2 import register_tools

__all__ = ["register_tools"]
