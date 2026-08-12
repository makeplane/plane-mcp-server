"""Tool surface selection.

`PLANE_MCP_TOOLS_VERSION` chooses the advertised catalogue:

    v2  one action-dispatch tool per resource (29 tools) -- the default
    v1  the original verb-per-endpoint catalogue (177 tools)

An environment variable rather than a CLI flag because the default is the
desired state: a lost variable yields v2, and it is settable on every transport
including hosted.

"tools v2" is the shape of the MCP surface. It is unrelated to Plane's API v2,
which is a backend concern and will be selected separately.

v1 is removed in the next major release.
"""

from __future__ import annotations

import os

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger

DEFAULT_VERSION = "v2"
VERSIONS = ("v1", "v2")
ENV_VAR = "PLANE_MCP_TOOLS_VERSION"

logger = get_logger(__name__)


def selected_version() -> str:
    """Read and validate the requested surface version."""
    version = os.getenv(ENV_VAR, DEFAULT_VERSION).strip().lower() or DEFAULT_VERSION
    if version not in VERSIONS:
        raise ValueError(f"{ENV_VAR} must be one of {VERSIONS}, got {version!r}")
    return version


def register_tools(mcp: FastMCP) -> None:
    """Attach the selected tool surface to `mcp`."""
    if selected_version() == "v1":
        from plane_mcp.tools.v1 import register_tools as register

        logger.warning(
            "%s=v1 serves the legacy 177-tool surface, which is removed in the next "
            "major release. Unset it to use the consolidated surface.",
            ENV_VAR,
        )
        register(mcp)
        return

    from plane_mcp.tools.v2 import register_tools as register

    register(mcp)
