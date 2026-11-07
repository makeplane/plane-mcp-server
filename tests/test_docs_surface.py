"""Unit tests for the /mcp/docs intent-based tool surface and LOD."""

import asyncio
from unittest.mock import MagicMock, patch

from fastmcp import FastMCP

from plane_mcp.docs.lod import DocsLOD, apply_doc_lod
from plane_mcp.docs.tools import register_docs_tools


def test_docs_lod_transformations():
    """Verify DocsLOD summary, standard, and full transformations and field filtering."""
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
    """Verify search_docs discovery output when project_slug='help' is passed."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws")

    p1 = MagicMock(identifier="ENG")
    c1 = MagicMock()
    c1.name = "Engineering Docs"
    mock_client.projects.list.return_value = MagicMock(results=[p1])
    mock_client.collections.list.return_value = MagicMock(results=[c1])

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)

    tool_fn = asyncio.run(mcp.get_tool("search_docs")).fn
    res = tool_fn(project_slug="help")

    assert "ENG" in res
    assert "Engineering Docs" in res


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_create_and_read_doc(mock_get_ctx):
    """Verify creating and reading a workspace documentation page."""
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
    """Verify snippet replacement in update_doc preserves surrounding HTML and sends updated payload."""
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

    update_fn = asyncio.run(mcp.get_tool("update_doc")).fn
    res = update_fn(
        page=page_uuid,
        replace_target_snippet="Step 1: Build",
        replace_text="Step 1: Test and Build",
    )
    assert "Test and Build" in res
    mock_client.pages.update_workspace_page.assert_called_once()
    sent_html = mock_client.pages.update_workspace_page.call_args.kwargs["data"].description_html
    assert "Step 1: Test and Build" in sent_html
    assert "Step 2: Deploy" in sent_html


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_search_docs_scoped_query_filter_and_uuid_ticket(mock_get_ctx):
    """Verify search_docs applies query filtering to collection and ticket-scoped pages and resolves ticket UUIDs."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws-scoped")

    coll_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    mock_client.collections.pages.list.return_value = MagicMock(
        results=[
            {"id": "p-1", "name": "Architecture Runbook"},
            {"id": "p-2", "name": "Onboarding Guide"},
        ],
        next_cursor=None,
        next_page_results=False,
    )

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)
    search_fn = asyncio.run(mcp.get_tool("search_docs")).fn

    res_coll = search_fn(collection=coll_uuid, query="runbook")
    assert "Architecture Runbook" in res_coll
    assert "Onboarding Guide" not in res_coll

    # Ticket UUID scope with query filter
    ticket_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    proj_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    mock_client.work_items._get.return_value = {"id": ticket_uuid, "project_id": proj_uuid}
    mock_client.work_items.pages.list.return_value = MagicMock(
        results=[MagicMock(page_id="p-1"), MagicMock(page_id="p-2")]
    )
    mock_client.pages.retrieve_project_page.side_effect = [
        {"id": "p-1", "name": "Incident Postmortem"},
        {"id": "p-2", "name": "Release Checklist"},
    ]

    res_ticket = search_fn(ticket_id=ticket_uuid, query="postmortem")
    assert "Incident Postmortem" in res_ticket
    assert "Release Checklist" not in res_ticket


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_search_docs_pagination(mock_get_ctx):
    """Verify search_docs follows pagination cursors across API pages until limit is satisfied."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws-paginated")

    page1 = MagicMock(
        results=[{"id": "p-1", "name": "Unrelated Doc"}],
        next_cursor="1:1:0",
        next_page_results=True,
    )
    page2 = MagicMock(
        results=[{"id": "p-2", "name": "Target Security Architecture"}],
        next_cursor=None,
        next_page_results=False,
    )
    mock_client.pages.list_workspace_pages.side_effect = [page1, page2]

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)
    search_fn = asyncio.run(mcp.get_tool("search_docs")).fn

    res = search_fn(query="Security", limit=5)
    assert "Target Security Architecture" in res
    assert mock_client.pages.list_workspace_pages.call_count == 2


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_create_doc_template_body_and_workspace_ticket_attachments(mock_get_ctx):
    """Verify create_doc seeds body from template and attaches tickets on workspace pages with failure reporting."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws-create")

    tmpl_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    tmpl_obj = MagicMock(id=tmpl_id, description_html="<h2>ADR Context</h2><p>Template details</p>")
    tmpl_obj.name = "ADR Template"
    mock_client.workspace_templates.pages.list.return_value = MagicMock(results=[tmpl_obj])

    page_uuid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    mock_client.pages.create_workspace_page.return_value = {
        "id": page_uuid,
        "name": "ADR-001",
        "description_html": "<h2>ADR Context</h2><p>Template details</p>",
    }

    proj_uuid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    proj_obj = MagicMock(id=proj_uuid, identifier="ENG", slug="eng")
    proj_obj.name = "Engineering"
    mock_client.projects.list.return_value = MagicMock(results=[proj_obj], next_page_results=False, next_cursor=None)
    mock_client.work_items._get.return_value = {"id": "12345678-1234-1234-1234-123456789012"}

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)
    create_fn = asyncio.run(mcp.get_tool("create_doc")).fn

    res = create_fn(
        title="ADR-001",
        template="ADR Template",
        ticket_ids=["ENG-1", "INVALID-TICKET-FORMAT-999"],
    )

    sent_data = mock_client.pages.create_workspace_page.call_args.kwargs["data"]
    assert "ADR Context" in sent_data.description_html
    assert "Template details" in sent_data.description_html
    mock_client.work_items.pages.create.assert_called_once()
    assert "ENG-1" in res
    assert "INVALID-TICKET-FORMAT-999" in res
    assert "partial" in res


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_update_doc_rejects_archive_combined_with_edits(mock_get_ctx):
    """Verify update_doc rejects requests that combine archive/unarchive with title, body, or collection edits."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws-archive-guard")

    page_uuid = "11111111-1111-1111-1111-111111111111"
    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)
    update_fn = asyncio.run(mcp.get_tool("update_doc")).fn

    res = update_fn(page=page_uuid, archive=True, new_title="Should Fail")
    assert "Cannot combine archive/unarchive with page edits" in res
    mock_client.pages.archive_workspace_page.assert_not_called()
    mock_client.pages.update_workspace_page.assert_not_called()


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_organize_docs_remove_page_and_create_template(mock_get_ctx):
    """Verify organize_docs supports collection remove_page and template create actions."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws-organize")

    coll_uuid = "11111111-2222-3333-4444-555555555555"
    page_uuid = "66666666-7777-8888-9999-000000000000"
    membership_uuid = "99999999-8888-7777-6666-555555555555"

    mock_client.collections.pages.list.return_value = MagicMock(
        results=[{"page_collection_id": membership_uuid, "page": {"id": page_uuid, "name": "Runbook"}}],
        next_cursor=None,
        next_page_results=False,
    )

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)
    organize_fn = asyncio.run(mcp.get_tool("organize_docs")).fn

    res_remove = organize_fn(
        target="collection",
        action="remove_page",
        name_or_id=coll_uuid,
        pages=[page_uuid],
    )
    assert "pages_removed" in res_remove
    mock_client.collections.pages.remove.assert_called_once_with(
        workspace_slug="test-ws-organize",
        collection_id=coll_uuid,
        page_collection_id=membership_uuid,
    )

    tmpl_uuid = "abababab-cdcd-efef-1212-343434343434"
    mock_client.workspace_templates.pages.create.return_value = {
        "id": tmpl_uuid,
        "name": "RFC Template",
    }
    res_tmpl = organize_fn(
        target="template",
        action="create",
        name_or_id="RFC Template",
        description="# Summary\n\nDetailed RFC body.",
    )
    assert tmpl_uuid in res_tmpl
    assert "created" in res_tmpl
    mock_client.workspace_templates.pages.create.assert_called_once()
    created_payload = mock_client.workspace_templates.pages.create.call_args.kwargs["data"]
    assert created_payload.name == "RFC Template"
    assert "<h1>Summary</h1>" in created_payload.description_html


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_link_doc_with_ticket_uuid(mock_get_ctx):
    """Verify link_doc resolves project context for a raw ticket UUID without splitting on hyphens."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws-link-uuid")

    ticket_uuid = "12121212-3434-5656-7878-909090909090"
    proj_uuid = "abababab-abab-abab-abab-abababababab"
    page_uuid = "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"

    mock_client.work_items._get.return_value = {
        "id": ticket_uuid,
        "project_id": proj_uuid,
        "project_detail": {"id": proj_uuid, "identifier": "ENG"},
    }

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)
    link_fn = asyncio.run(mcp.get_tool("link_doc")).fn

    res_attach = link_fn(ticket_id=ticket_uuid, page=page_uuid, action="attach")
    assert "attached" in res_attach
    mock_client.work_items.pages.create.assert_called_once()
    assert mock_client.work_items.pages.create.call_args.kwargs["project_id"] == proj_uuid
    assert mock_client.work_items.pages.create.call_args.kwargs["work_item_id"] == ticket_uuid
