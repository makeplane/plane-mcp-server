"""Read-only GraphQL access to Plane data, as a single tool.

One-to-one rather than consolidated: a single read with no siblings.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from plane_mcp import gql
from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_description
from plane_mcp.toolkit.runtime import needs

NAME = "graphql_query"
TITLE = "GraphQL query"

ACTIONS = (
    Action(
        "query",
        requires=("query",),
        optional=("variables", "operation_name"),
        note="this tool has no action parameter; read-only, queries only",
        read=True,
    ),
)

FOOTER = (
    "Fetch related data in one call, e.g. "
    '`{ workItem(identifier: "PLANE-1") { name state { name } labels { name } assignees { displayName } '
    "comments { text } } }`. Root fields: projects, project, workItem, workItems(project, pql, first, after). "
    "workItems takes a PQL filter (see get_pql_reference). Pass `variables` as a JSON object encoded as a string. "
    "Limits: depth 7, 200 Plane API calls per query, 100 items per page. There are no mutations."
)

LEGACY: dict[str, str] = {}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description(
            "Run a read-only GraphQL query over the Plane workspace (REST under the hood).", ACTIONS, FOOTER
        ),
        annotations=ToolAnnotations(
            title=TITLE, readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    def graphql_query(query: str = "", variables: str = "", operation_name: str = "") -> dict | str:
        if error := needs("query", query=query):
            return error
        parsed: dict[str, Any] | None = None
        if variables.strip():
            try:
                parsed = json.loads(variables)
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict):
                return "Error: variables must be a JSON object, passed as a string."
        client, workspace = get_plane_client_context()
        return gql.execute(gql.Context(client=client, workspace=workspace), query, parsed, operation_name or None)
