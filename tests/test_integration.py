"""
Simple integration test for Plane MCP Server.

Environment Variables Required:
    PLANE_TEST_API_KEY: API key for authentication
    PLANE_TEST_WORKSPACE_SLUG: Workspace slug for testing
    PLANE_TEST_MCP_URL: MCP server URL (default: http://localhost:8211)
"""

import asyncio
import os
import uuid

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


def get_config():
    """Load test configuration from environment."""
    api_key = os.getenv("PLANE_TEST_API_KEY", "")
    workspace_slug = os.getenv("PLANE_TEST_WORKSPACE_SLUG", "")
    mcp_url = os.getenv("PLANE_TEST_MCP_URL", "http://localhost:8211")

    if not api_key or not workspace_slug:
        raise RuntimeError(
            "Missing required env vars: PLANE_TEST_API_KEY, PLANE_TEST_WORKSPACE_SLUG"
        )

    return {
        "api_key": api_key,
        "workspace_slug": workspace_slug,
        "mcp_url": mcp_url,
    }


def extract_result(result):
    """Extract data from MCP tool result."""
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if hasattr(result, "content") and result.content:
        import json

        content = result.content[0]
        if hasattr(content, "text"):
            try:
                return json.loads(content.text)
            except:
                return {"raw": content.text}
    return {}


async def run_integration_test():
    """
    Full integration test:
    1. Create a project
    2. Create work item 1
    3. Create work item 2
    4. Update work item 2 with work item 1 as parent
    5. Delete work items
    6. Delete project
    """
    config = get_config()
    unique_id = uuid.uuid4().hex[:6]

    transport = StreamableHttpTransport(
        f"{config['mcp_url']}/http/api-key/mcp",
        headers={
            "x-workspace-slug": config["workspace_slug"],
            "authorization": f"Bearer {config['api_key']}",
        },
    )

    async with Client(transport=transport) as client:
        # 1. Create project
        print(f"Creating project...")
        project_result = await client.call_tool(
            "create_project",
            {
                "name": f"Test Project {unique_id}",
                "identifier": f"TP{unique_id[:3].upper()}",
                "description": "Integration test project",
            },
        )
        project = extract_result(project_result)
        project_id = project["id"]
        print(f"Created project: {project_id}")

        # 2. Create work item 1
        print(f"Creating work item 1...")
        work_item_1_result = await client.call_tool(
            "create_work_item",
            {
                "project_id": project_id,
                "name": f"Parent Work Item {unique_id}",
            },
        )
        work_item_1 = extract_result(work_item_1_result)
        work_item_1_id = work_item_1["id"]
        print(f"Created work item 1: {work_item_1_id}")

        # 3. Create work item 2
        print(f"Creating work item 2...")
        work_item_2_result = await client.call_tool(
            "create_work_item",
            {
                "project_id": project_id,
                "name": f"Child Work Item {unique_id}",
            },
        )
        work_item_2 = extract_result(work_item_2_result)
        work_item_2_id = work_item_2["id"]
        print(f"Created work item 2: {work_item_2_id}")

        # 4. Update work item 2 with work item 1 as parent
        print(f"Setting parent relationship...")
        await client.call_tool(
            "update_work_item",
            {
                "project_id": project_id,
                "work_item_id": work_item_2_id,
                "parent": work_item_1_id,
            },
        )
        print(f"Set work item 1 as parent of work item 2")

        # 5. Delete work items
        print(f"Deleting work items...")
        await client.call_tool(
            "delete_work_item",
            {"project_id": project_id, "work_item_id": work_item_2_id},
        )
        print(f"Deleted work item 2")

        await client.call_tool(
            "delete_work_item",
            {"project_id": project_id, "work_item_id": work_item_1_id},
        )
        print(f"Deleted work item 1")

        # 6. Delete project
        print(f"Deleting project...")
        await client.call_tool("delete_project", {"project_id": project_id})
        print(f"Deleted project")

        print("Integration test passed!")


def test_full_integration():
    """Pytest entry point - runs the async integration test."""
    asyncio.run(run_integration_test())


# Expected tools that should be registered with the MCP server
EXPECTED_TOOLS = [
    # Project tools
    "create_project",
    "list_projects",
    "retrieve_project",
    "update_project",
    "delete_project",
    # Work item tools
    "create_work_item",
    "list_work_items",
    "retrieve_work_item",
    "update_work_item",
    "delete_work_item",
    # Label tools
    "list_labels",
    "create_label",
    "retrieve_label",
    "update_label",
    "delete_label",
    # State tools
    "list_states",
    "create_state",
    "retrieve_state",
    "update_state",
    "delete_state",
    # Page tools
    "retrieve_workspace_page",
    "retrieve_project_page",
    "create_workspace_page",
    "create_project_page",
    # Work item activity tools
    "list_work_item_activities",
    "retrieve_work_item_activity",
    # Work item comment tools
    "list_work_item_comments",
    "retrieve_work_item_comment",
    "create_work_item_comment",
    "update_work_item_comment",
    "delete_work_item_comment",
    # Work item link tools
    "list_work_item_links",
    "retrieve_work_item_link",
    "create_work_item_link",
    "update_work_item_link",
    "delete_work_item_link",
    # Work item relation tools
    "list_work_item_relations",
    "create_work_item_relation",
    "remove_work_item_relation",
    # Work item type tools
    "list_work_item_types",
    "create_work_item_type",
    "retrieve_work_item_type",
    "update_work_item_type",
    "delete_work_item_type",
    # Work log tools
    "list_work_logs",
    "create_work_log",
    "update_work_log",
    "delete_work_log",
    # Workspace tools
    "get_workspace_members",
    "get_workspace_features",
    "update_workspace_features",
    # Cycle tools
    "list_cycles",
    "create_cycle",
    "retrieve_cycle",
    "update_cycle",
    "delete_cycle",
    "list_archived_cycles",
    "add_work_items_to_cycle",
    "remove_work_item_from_cycle",
    "list_cycle_work_items",
    "transfer_cycle_work_items",
    "archive_cycle",
    "unarchive_cycle",
    # Module tools
    "list_modules",
    "create_module",
    "retrieve_module",
    "update_module",
    "delete_module",
    "list_archived_modules",
    "add_work_items_to_module",
    "remove_work_item_from_module",
    "list_module_work_items",
    "archive_module",
    "unarchive_module",
    # Initiative tools
    "list_initiatives",
    "create_initiative",
    "retrieve_initiative",
    "update_initiative",
    "delete_initiative",
    # Intake tools
    "list_intake_work_items",
    "create_intake_work_item",
    "retrieve_intake_work_item",
    "update_intake_work_item",
    "delete_intake_work_item",
    # User tools
    "get_me",
    # Work item property tools
    "list_work_item_properties",
    "create_work_item_property",
    "retrieve_work_item_property",
    "update_work_item_property",
    "delete_work_item_property",
]


async def run_tools_availability_test():
    """
    Test that all expected tools are available on the MCP server.
    This test verifies that all registered tools are exposed correctly.
    """
    config = get_config()

    transport = StreamableHttpTransport(
        f"{config['mcp_url']}/http/api-key/mcp",
        headers={
            "x-workspace-slug": config["workspace_slug"],
            "authorization": f"Bearer {config['api_key']}",
        },
    )

    async with Client(transport=transport) as client:
        # Get list of available tools
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}

        print(f"Found {len(tool_names)} tools on the server")

        # Check that all expected tools are available
        missing_tools = []
        for expected_tool in EXPECTED_TOOLS:
            if expected_tool not in tool_names:
                missing_tools.append(expected_tool)

        if missing_tools:
            print(f"Missing tools: {missing_tools}")
            raise AssertionError(f"The following expected tools are not available: {missing_tools}")

        print(f"All {len(EXPECTED_TOOLS)} expected tools are available!")
        print("Tools availability test passed!")


def test_tools_availability():
    """Pytest entry point - verifies all expected tools are registered."""
    asyncio.run(run_tools_availability_test())


if __name__ == "__main__":
    asyncio.run(run_integration_test())
