"""On-demand PQL syntax reference.

Relocates the Plane Query Language reference out of every list tool's schema
(saved ~5,500 tokens per `tools/list` manifest call) and behind an on-demand
tool. The 5 PQL-enabled list tools (`list_work_items`,
`list_workspace_work_items`, `list_archived_work_items`,
`list_cycle_work_items`, `list_module_work_items`) carry only a one-line hint
pointing to this tool; full syntax is fetched on demand.

Note: the error-recovery payload on a failed PQL query still inlines
`PQL_FULL_REFERENCE` so a single round-trip self-correction loop is preserved
even if the model never calls `get_pql_reference` proactively.
"""

from typing import Literal

from fastmcp import FastMCP

from plane_mcp.pql_reference import PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE


def register_pql_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def get_pql_reference(detail: Literal["brief", "full"] = "full") -> dict:
        """
        Return the Plane Query Language (PQL) syntax reference.

        Call this when composing the `pql` filter for `list_work_items`,
        `list_workspace_work_items`, `list_archived_work_items`,
        `list_cycle_work_items`, or `list_module_work_items`.

        Args:
            detail: "full" (default) returns the comprehensive reference with
                all operators, functions, common mistakes, and worked examples.
                "brief" returns the compact field/operator/function quick
                reference (lighter payload for simple queries).

        Returns:
            Dict with `detail` (which version was returned) and `reference`
            (the PQL syntax text).
        """
        if detail == "brief":
            return {"detail": "brief", "reference": PQL_FIELD_DESCRIPTION}
        return {"detail": "full", "reference": PQL_FULL_REFERENCE}
