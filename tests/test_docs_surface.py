"""Unit tests for the /mcp/docs intent-based tool surface and LOD."""

from unittest.mock import MagicMock, patch

from fastmcp import FastMCP

from plane_mcp.docs.lod import DocsLOD, apply_doc_lod
from plane_mcp.docs.tools import register_docs_tools


def test_docs_lod_transformations():
    page_data = {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Architecture Overview",
        "description_html": "<h1>System Architecture</h1><p>Plane MCP architecture.</p>",
        "parent_id": None,
        "collection_id": "c-1",
        "is_locked": False,
        "updated_at": "2026-10-29T12:00:00Z",
        "extra_field": "secret",
    }

    summary = apply_doc_lod(page_data, lod=DocsLOD.SUMMARY, project_slug="TEST")
    assert summary["id"] == "11111111-1111-1111-1111-111111111111"
    assert summary["name"] == "Architecture Overview"
    assert summary["scope"] == "TEST"
    assert summary["collection_id"] == "c-1"
    assert "content" not in summary
    assert "extra_field" not in summary

    standard = apply_doc_lod(page_data, lod=DocsLOD.STANDARD, project_slug=None)
    assert standard["scope"] == "workspace"
    assert "# System Architecture" in standard["content"]
    assert "Plane MCP architecture." in standard["content"]
    assert "extra_field" not in standard

    full = apply_doc_lod(page_data, lod=DocsLOD.FULL)
    assert full["extra_field"] == "secret"
    assert "# System Architecture" in full["content"]


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_search_docs_discovery(mock_get_ctx):
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws")

    p1 = MagicMock(identifier="ENG")
    c1 = MagicMock()
    c1.name = "Engineering Docs"
    mock_client.projects.list.return_value = MagicMock(results=[p1])
    mock_client.collections.list.return_value = MagicMock(results=[c1])

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)

    import asyncio

    tool_fn = asyncio.run(mcp.get_tool("search_docs")).fn
    res = tool_fn(project_slug="help")

    assert "ENG" in res
    assert "Engineering Docs" in res


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_create_and_read_doc(mock_get_ctx):
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws")

    page_uuid = "11111111-1111-1111-1111-111111111111"
    created_page = {
        "id": page_uuid,
        "name": "Runbook",
        "description_html": "<p>Steps to deploy</p>",
        "parent_id": None,
        "collection_id": None,
        "is_locked": False,
        "updated_at": "2026-10-29T12:00:00Z",
    }
    mock_client.pages.create_workspace_page.return_value = created_page
    mock_client.pages.retrieve_workspace_page.return_value = created_page

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)

    import asyncio

    create_fn = asyncio.run(mcp.get_tool("create_doc")).fn
    res_create = create_fn(title="Runbook", content="Steps to deploy")
    assert "Runbook" in res_create
    assert "Steps to deploy" in res_create

    read_fn = next(t.fn for t in [asyncio.run(mcp.get_tool("read_doc"))])
    res_read = read_fn(page=page_uuid)
    assert "Runbook" in res_read
    assert "Steps to deploy" in res_read


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_update_doc_snippet(mock_get_ctx):
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws")

    page_uuid = "11111111-1111-1111-1111-111111111111"
    current = {
        "id": page_uuid,
        "name": "Runbook",
        "description_html": "<p>Step 1: Build<br>Step 2: Deploy</p>",
    }
    updated = {
        "id": page_uuid,
        "name": "Runbook",
        "description_html": "<p>Step 1: Test and Build<br>Step 2: Deploy</p>",
    }
    mock_client.pages.retrieve_workspace_page.return_value = current
    mock_client.pages.update_workspace_page.return_value = updated

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)

    import asyncio

    update_fn = asyncio.run(mcp.get_tool("update_doc")).fn
    res = update_fn(
        page=page_uuid,
        replace_target_snippet="Step 1: Build",
        replace_text="Step 1: Test and Build",
    )
    assert "Test and Build" in res
