"""State-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.states import PaginatedStateResponse

from plane_mcp.client import get_plane_client_context
from plane_mcp.models import StateSummary


def _enum_str(v) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def register_state_tools(mcp: FastMCP) -> None:
    """Register all state-related tools with the MCP server."""

    @mcp.tool()
    def list_states(
        project_id: str,
    ) -> list[StateSummary]:
        """
        List all states for a project.

        Use this to get state UUIDs needed when creating or updating work items.

        Args:
            project_id: UUID of the project

        Returns:
            List of StateSummary objects containing id, name, group, color, default, sequence.
        """
        client, workspace_slug = get_plane_client_context()

        response: PaginatedStateResponse = client.states.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
        )

        return [
            StateSummary(
                id=s.id,
                name=s.name,
                group=_enum_str(s.group),
                default=s.default,
            ).slim()
            for s in response.results
        ]
