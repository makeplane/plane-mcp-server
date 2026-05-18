"""Tests for module tools."""

import asyncio

from fastmcp import Client, FastMCP

from plane_mcp.tools import modules as module_tools


class FakeModulesClient:
    def __init__(self) -> None:
        self.add_work_items_calls: list[dict[str, object]] = []

    def add_work_items(
        self,
        workspace_slug: str,
        project_id: str,
        module_id: str,
        issue_ids: list[str],
    ) -> list[dict[str, str]]:
        self.add_work_items_calls.append(
            {
                "workspace_slug": workspace_slug,
                "project_id": project_id,
                "module_id": module_id,
                "issue_ids": issue_ids,
            }
        )
        return [{"id": "module-issue-id", "module": module_id, "issue": issue_ids[0]}]


class FakePlaneClient:
    def __init__(self) -> None:
        self.modules = FakeModulesClient()


def test_add_work_items_to_module_returns_success_payload(monkeypatch) -> None:
    """A successful SDK list response should not leak into MCP response validation."""
    fake_client = FakePlaneClient()
    monkeypatch.setattr(
        module_tools,
        "get_plane_client_context",
        lambda: (fake_client, "test-workspace"),
    )

    async def call_tool() -> object:
        mcp = FastMCP("test")
        module_tools.register_module_tools(mcp)

        async with Client(mcp) as client:
            result = await client.call_tool(
                "add_work_items_to_module",
                {
                    "project_id": "project-id",
                    "module_id": "module-id",
                    "work_item_ids": ["work-item-id"],
                },
            )
            return result.structured_content

    assert asyncio.run(call_tool()) == {"success": True}
    assert fake_client.modules.add_work_items_calls == [
        {
            "workspace_slug": "test-workspace",
            "project_id": "project-id",
            "module_id": "module-id",
            "issue_ids": ["work-item-id"],
        }
    ]
