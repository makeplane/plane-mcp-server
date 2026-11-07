"""Comprehensive branch and edge-case unit tests targeting 100% test coverage
for plane_mcp newly introduced modules:
- plane_mcp.yaml_formatter
- plane_mcp.resolver
- plane_mcp.sanitize
- plane_mcp.docs
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent
from plane.errors.errors import HttpError

from plane_mcp.docs.lod import (
    DocsLOD,
    apply_doc_lod,
)
from plane_mcp.docs.tools import register_docs_tools
from plane_mcp.resolver import (
    EntityResolutionError,
    EntityResolver,
)
from plane_mcp.sanitize import (
    apply_snippet_edits,
    html_to_clean_markdown,
)
from plane_mcp.yaml_formatter import (
    InlineSafeDumper,
    YamlOutputMiddleware,
    _clean_and_convert,
    format_as_yaml,
)

# ---------------------------------------------------------------------------
# 1. Tests for plane_mcp.docs.lod
# ---------------------------------------------------------------------------


class DummyWithGoodModelDump:
    """Dummy model implementing a working model_dump method."""

    def model_dump(self, mode="json"):
        """Return a valid dictionary payload."""
        return {"id": "good-1", "name": "GoodModel", "description_html": "<p>Good</p>"}


class DummyWithBrokenModelDump:
    """Dummy model raising RuntimeError from model_dump."""

    def model_dump(self, mode="json"):
        """Raise RuntimeError to test fallback handling."""
        raise RuntimeError("model_dump failed")


class DummyWithDictMethod:
    """Dummy model implementing a working legacy dict method."""

    def dict(self):
        """Return a valid dictionary payload."""
        return {"id": "d-1", "name": "DictModel", "description_html": "<p>Dict</p>"}


class DummyWithBrokenDict:
    """Dummy model raising RuntimeError from dict."""

    def dict(self):
        """Raise RuntimeError to test fallback handling."""
        raise RuntimeError("dict failed")


class DummyPlainObject:
    """Plain object with attributes and no serialization methods."""

    def __init__(self):
        """Initialize plain object attributes."""
        self.id = "p-plain"
        self.name = "Plain"
        self.description_html = "Plain text"
        self.parent_id = None
        self.collection_id = None
        self.is_locked = False
        self.updated_at = "2026-10-29"
        self.access = "public"
        self.created_at = "2026-10-29"
        self.created_by = "user-1"


def test_docs_lod_edge_cases():
    """Verify apply_doc_lod handles model_dump, dict, broken methods, and plain attribute objects."""
    # Good model_dump returning dict
    good_md = DummyWithGoodModelDump()
    res_g = apply_doc_lod(good_md)
    assert res_g["name"] == "GoodModel"

    # Broken model_dump falls back
    broken_md = DummyWithBrokenModelDump()
    res_b = apply_doc_lod(broken_md)
    assert isinstance(res_b, dict)

    # dict() method returning dict
    dict_obj = DummyWithDictMethod()
    res_d = apply_doc_lod(dict_obj)
    assert res_d["name"] == "DictModel"

    # Broken dict falls back
    broken_d = DummyWithBrokenDict()
    res_bd = apply_doc_lod(broken_d)
    assert isinstance(res_bd, dict)

    # Plain object falling to getattr
    plain = DummyPlainObject()
    res_p = apply_doc_lod(plain, lod=DocsLOD.SUMMARY)
    assert res_p["id"] == "p-plain"
    assert res_p["name"] == "Plain"


# ---------------------------------------------------------------------------
# 2. Tests for plane_mcp.sanitize
# ---------------------------------------------------------------------------


def test_sanitize_edge_cases():
    """Verify html_to_clean_markdown fallback and apply_snippet_edits HTML entity matching."""
    # Exception inside html_to_clean_markdown
    with patch("plane_mcp.sanitize.md", side_effect=RuntimeError("md engine crash")):
        fallback = html_to_clean_markdown("<b>Fallback HTML</b>")
        assert fallback == "<b>Fallback HTML</b>"

    # HTML entity matching in replace_target_snippet
    # replace_target_snippet with & unescaped: &amp; in current, "&" in snippet
    html_with_amp = "<p>Text with & entity</p>"
    out, err = apply_snippet_edits(
        html_with_amp,
        replace_target_snippet="with &amp; entity",
        replace_text="with AND entity",
    )
    assert err is None
    assert "with AND entity" in out

    # HTML entity matching in append_after_snippet
    out, err = apply_snippet_edits(
        html_with_amp,
        append_after_snippet="with &amp; entity",
        append_text=" [appended]",
    )
    assert err is None
    assert "[appended]" in out

    # append_text with empty current_html
    out, err = apply_snippet_edits("", append_text="Brand new content")
    assert err is None
    assert "Brand new content" in out


# ---------------------------------------------------------------------------
# 3. Tests for plane_mcp.yaml_formatter
# ---------------------------------------------------------------------------


def test_yaml_formatter_edge_cases():
    """Verify InlineSafeDumper alias suppression, model serialization helpers, and empty dict formatting."""
    # InlineSafeDumper.ignore_aliases
    dumper = InlineSafeDumper(stream=MagicMock())
    assert dumper.ignore_aliases({"a": 1}) is True

    # _clean_and_convert with model_dump()
    good_md = DummyWithGoodModelDump()
    assert _clean_and_convert(good_md)["name"] == "GoodModel"

    # _clean_and_convert with dict() method
    dict_obj = DummyWithDictMethod()
    converted = _clean_and_convert(dict_obj)
    assert converted["name"] == "DictModel"

    # format_as_yaml invalid JSON
    inv_res = format_as_yaml("{not valid json: true}")
    assert inv_res == "{not valid json: true}"

    # format_as_yaml empty dict result
    empty_res = format_as_yaml({"none_field": None, "empty_list": []})
    assert empty_res == "{}"


@pytest.mark.asyncio
async def test_yaml_output_middleware_exhaustive():
    """Verify YamlOutputMiddleware converts TextContent and raw dictionaries while preserving non-text blocks."""
    middleware = YamlOutputMiddleware()
    ctx = MagicMock()

    # Case 1: ToolResult with TextContent and non-TextContent
    dummy_block = ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")
    result_with_mixed = ToolResult(
        content=[TextContent(type="text", text='{"title": "Test"}'), dummy_block],
        meta={"execution_time": 0.1},
    )

    async def call_next_mixed(_):
        """Return mixed ToolResult content."""
        return result_with_mixed

    res1 = await middleware.on_call_tool(ctx, call_next_mixed)
    assert isinstance(res1, ToolResult)
    assert "title: Test" in res1.content[0].text
    assert res1.content[1] is dummy_block

    # Case 2: call_next returns non-ToolResult (raw dict)
    async def call_next_raw(_):
        """Return raw dictionary payload."""
        return {"status": "ok", "items": ["a", "b"]}

    res2 = await middleware.on_call_tool(ctx, call_next_raw)
    assert isinstance(res2, ToolResult)
    assert "status: ok" in res2.content[0].text
    assert "[a, b]" in res2.content[0].text


# ---------------------------------------------------------------------------
# 4. Tests for plane_mcp.resolver
# ---------------------------------------------------------------------------


def test_resolver_exceptions_and_edge_branches():
    """Verify EntityResolver error handling and caching across projects, states, tickets, pages, and templates."""
    mock_client = MagicMock()
    resolver = EntityResolver(mock_client, "test-ws")

    # 1. Project list failure (non-EntityResolutionError)
    mock_client.projects.list.side_effect = RuntimeError("Network timeout")
    with pytest.raises(RuntimeError, match="Failed to fetch projects"):
        resolver.resolve_project("NONEXISTENT")

    # 2. State list failure (non-EntityResolutionError)
    mock_client.projects.list.side_effect = None
    p_mock = MagicMock(identifier="ENG", id="11111111-1111-1111-1111-111111111111")
    p_mock.name = "Eng"
    p_mock.slug = "eng"
    mock_client.projects.list.return_value = MagicMock(results=[p_mock])

    mock_client.states.list.side_effect = RuntimeError("Database down")
    with pytest.raises(RuntimeError, match="Failed to fetch states"):
        resolver.resolve_state("ENG", "In Progress")

    # 3. Ticket resolution without ID in response
    mock_client.work_items._get.return_value = {"no_id": True}
    with pytest.raises(EntityResolutionError, match="Ticket 'ENG-1' not found"):
        resolver.resolve_ticket("ENG-1")

    # 4. Ticket resolution HttpError 500
    mock_client.work_items._get.side_effect = HttpError(status_code=500, message="Internal Server Error")
    with pytest.raises(RuntimeError, match="Failed to retrieve ticket"):
        resolver.resolve_ticket("ENG-2")

    # 5. Ticket resolution generic Exception
    mock_client.work_items._get.side_effect = ValueError("Corrupt JSON")
    with pytest.raises(RuntimeError, match="Failed to retrieve ticket"):
        resolver.resolve_ticket("ENG-3")

    # 6. Page resolution with project_slug found by name
    p_spec = MagicMock(id="p-proj-1")
    p_spec.name = "Project Spec"
    mock_client.pages.list_project_pages.return_value = MagicMock(results=[p_spec])
    pid, pr_id = resolver.resolve_page("Project Spec", project_slug="ENG")
    assert pid == "p-proj-1"
    assert pr_id == "11111111-1111-1111-1111-111111111111"

    # 7. Page resolution with project_slug NOT found by name
    mock_client.pages.list_project_pages.return_value = MagicMock(results=[])
    with pytest.raises(EntityResolutionError, match="Page 'Missing Spec' not found in project 'ENG'"):
        resolver.resolve_page("Missing Spec", project_slug="ENG")

    # 8. Workspace page resolution NOT found by name
    mock_client.pages.list_workspace_pages.return_value = MagicMock(results=[])
    with pytest.raises(EntityResolutionError, match="Page 'Missing Workspace' not found in workspace"):
        resolver.resolve_page("Missing Workspace")

    # 8a. Workspace page resolution found by name and cached
    mock_client.pages.list_workspace_pages.side_effect = None
    p_ws = MagicMock(id="p-ws-1")
    p_ws.name = "Company Handbook"
    mock_client.pages.list_workspace_pages.return_value = MagicMock(results=[p_ws])
    ws_pid, ws_pr_id = resolver.resolve_page("Company Handbook")
    assert ws_pid == "p-ws-1"
    assert ws_pr_id is None
    # Cache hit
    assert resolver.resolve_page("Company Handbook") == ("p-ws-1", None)

    # 9. Page resolution Exception
    mock_client.pages.list_workspace_pages.side_effect = RuntimeError("Disk IO Error")
    with pytest.raises(RuntimeError, match="Failed to resolve page"):
        resolver.resolve_page("Any Page")

    # 10. Collection NOT found by name
    c_item = MagicMock(id="c-uuid-1")
    c_item.name = "Engineering"
    mock_client.collections.list.return_value = MagicMock(results=[c_item])
    with pytest.raises(EntityResolutionError, match="Collection 'Missing Coll' not found"):
        resolver.resolve_collection("Missing Coll")

    # 11. Collection resolution Exception
    mock_client.collections.list.side_effect = RuntimeError("Collection API Crash")
    with pytest.raises(RuntimeError, match="Failed to resolve collection"):
        resolver.resolve_collection("Uncached Coll")

    # 12. Template with project_slug
    t_item = MagicMock(id="tmpl-p1")
    t_item.name = "Project Template"
    mock_client.project_templates.page_templates.list.return_value = MagicMock(results=[t_item])
    tmpl_id = resolver.resolve_template("Project Template", kind="page", project_slug="ENG")
    assert tmpl_id == "tmpl-p1"

    # 13. Template NOT found by name
    mock_client.project_templates.page_templates.list.return_value = MagicMock(results=[])
    with pytest.raises(EntityResolutionError, match="Template 'Missing Template' not found"):
        resolver.resolve_template("Missing Template", kind="page", project_slug="ENG")

    # 14. Template resolution Exception
    mock_client.project_templates.page_templates.list.side_effect = RuntimeError("Template API error")
    with pytest.raises(RuntimeError, match="Failed to resolve template"):
        resolver.resolve_template("Any Template", kind="page", project_slug="ENG")


# ---------------------------------------------------------------------------
# 5. Tests for plane_mcp.docs.tools
# ---------------------------------------------------------------------------


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_docs_tools_exhaustive_branches(mock_get_ctx):
    """Verify docs tools handle project scoping, snippet errors, attach/detach validation, and organization."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws")

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)

    p_eng = MagicMock(identifier="ENG", id="11111111-1111-1111-1111-111111111111")
    p_eng.name = "Engineering"
    p_eng.slug = "eng"
    mock_client.projects.list.return_value = MagicMock(results=[p_eng])

    # Tools under test
    search_fn = asyncio.run(mcp.get_tool("search_docs")).fn
    read_fn = asyncio.run(mcp.get_tool("read_doc")).fn
    create_fn = asyncio.run(mcp.get_tool("create_doc")).fn
    update_fn = asyncio.run(mcp.get_tool("update_doc")).fn
    link_fn = asyncio.run(mcp.get_tool("link_doc")).fn
    organize_fn = asyncio.run(mcp.get_tool("organize_docs")).fn

    # 1. search_docs with project_slug and query matching & non-matching
    p_match = MagicMock(id="p-m1", description_html="<p>Match</p>")
    p_match.name = "Architecture Guide"
    p_match.parent_id = None
    p_match.collection_id = None
    p_match.is_locked = False
    p_match.updated_at = "2026-10-29"

    p_nomatch = MagicMock(id="p-m2", description_html="<p>No</p>")
    p_nomatch.name = "Unrelated Guide"
    p_nomatch.parent_id = None
    p_nomatch.collection_id = None
    p_nomatch.is_locked = False
    p_nomatch.updated_at = "2026-10-29"

    mock_client.pages.list_project_pages.return_value = MagicMock(results=[p_match, p_nomatch])

    res_search_proj = search_fn(project_slug="ENG", query="Architecture")
    assert "Architecture Guide" in str(res_search_proj)
    assert "Unrelated Guide" not in str(res_search_proj)

    # 2. search_docs workspace pages with query matching & non-matching
    mock_client.pages.list_workspace_pages.return_value = MagicMock(results=[p_match, p_nomatch])
    res_search_ws = search_fn(query="Architecture")
    assert "Architecture Guide" in str(res_search_ws)

    # 3. read_doc with project_slug
    mock_client.pages.retrieve_project_page.return_value = p_match
    res_read_proj = read_fn(page="11111111-1111-1111-1111-111111111111", project_slug="ENG")
    assert "Architecture Guide" in str(res_read_proj)

    # 4. create_doc with parent_page, collection, template, ticket_ids (both success and fail)
    c_mock = MagicMock(id="22222222-2222-2222-2222-222222222222")
    c_mock.name = "Engineering Docs"
    mock_client.collections.list.return_value = MagicMock(results=[c_mock])

    t_mock = MagicMock(id="33333333-3333-3333-3333-333333333333")
    t_mock.name = "Design Doc"
    mock_client.project_templates.page_templates.list.return_value = MagicMock(results=[t_mock])

    mock_client.work_items._get.side_effect = [
        {"id": "wid-ok"},
        RuntimeError("ticket fail"),
    ]

    p_created = MagicMock(id="p-new-uuid", description_html="<p>New</p>")
    p_created.name = "New Spec"
    p_created.parent_id = None
    p_created.collection_id = None
    p_created.is_locked = False
    p_created.updated_at = "2026-10-29"
    mock_client.pages.create_project_page.return_value = p_created

    res_create = create_fn(
        title="New Spec",
        project_slug="ENG",
        parent_page="11111111-1111-1111-1111-111111111111",
        collection="Engineering Docs",
        template="Design Doc",
        ticket_ids=["ENG-1", "ENG-2"],
    )
    assert "New Spec" in str(res_create)
    assert "ENG-1" in str(res_create)

    # 5. update_doc: delete_permanently with project_slug
    p_uuid = "11111111-1111-1111-1111-111111111111"
    res_del_proj = update_fn(page=p_uuid, project_slug="ENG", delete_permanently=True)
    assert "deleted" in str(res_del_proj)

    # 6. update_doc: archive & unarchive with project_slug
    res_arch_proj = update_fn(page=p_uuid, project_slug="ENG", archive=True)
    assert "archived" in str(res_arch_proj)
    res_unarch_proj = update_fn(page=p_uuid, project_slug="ENG", archive=False)
    assert "unarchived" in str(res_unarch_proj)

    # 7. update_doc: overwrite_content, collection, and snippet error
    mock_client.pages.retrieve_project_page.return_value = MagicMock(description_html="<p>Original body</p>")
    p_updated = MagicMock(id=p_uuid, description_html="<p>Overwritten</p>")
    p_updated.name = "Updated Spec"
    p_updated.parent_id = None
    p_updated.collection_id = None
    p_updated.is_locked = False
    p_updated.updated_at = "2026-10-29"
    mock_client.pages.update_project_page.return_value = p_updated

    res_update_proj = update_fn(
        page=p_uuid,
        project_slug="ENG",
        overwrite_content="Overwritten",
        collection="Engineering Docs",
    )
    assert "Overwritten" in str(res_update_proj)

    # Snippet error branch
    res_snip_err = update_fn(
        page=p_uuid,
        project_slug="ENG",
        replace_target_snippet="nonexistent string",
        replace_text="something",
    )
    assert "error" in str(res_snip_err)
    assert "Target snippet not found" in str(res_snip_err)

    # 8. link_doc: error when page parameter is missing for attach
    res_link_nopage = link_fn(ticket_id="ENG-1", action="attach")
    assert "requires page parameter" in res_link_nopage

    # link_doc: error when page to detach is not linked
    mock_client.work_items._get.side_effect = None
    mock_client.work_items._get.return_value = {"id": "wid-ok"}
    mock_client.work_items.pages.list.return_value = MagicMock(results=[])
    res_detach_notfound = link_fn(ticket_id="ENG-1", page=p_uuid, action="detach")
    assert "is not linked to ticket" in res_detach_notfound

    # link_doc: unknown action
    res_link_unknown = link_fn(ticket_id="ENG-1", page=p_uuid, action="invalid_action")
    assert "Unknown action" in res_link_unknown

    # 9. organize_docs: missing name_or_id
    res_org_noname = organize_fn(target="collection", action="delete")
    assert "requires name_or_id" in res_org_noname

    # organize_docs: add_pages with empty pages
    res_org_nopages = organize_fn(target="collection", action="add_pages", name_or_id=c_mock.id, pages=[])
    assert "requires pages list" in res_org_nopages

    # organize_docs: template delete without name_or_id
    res_tmpl_noname = organize_fn(target="template", action="delete")
    assert "requires name_or_id" in res_tmpl_noname

    # organize_docs: unsupported target/action combination
    res_org_unsupp = organize_fn(target="template", action="add_pages")
    assert "Unsupported combination" in res_org_unsupp


def test_server_stdio_and_redirect_uris(monkeypatch):
    """Verify get_allowed_client_redirect_uris and get_stdio_mcp across all supported surfaces."""
    from plane_mcp.server import get_allowed_client_redirect_uris, get_stdio_mcp

    # Test get_allowed_client_redirect_uris with extra URIs
    monkeypatch.setenv("PLANE_OAUTH_ALLOWED_REDIRECT_URIS", "https://extra.com/cb, , http://localhost:*")
    uris = get_allowed_client_redirect_uris()
    assert "https://extra.com/cb" in uris

    # Test get_stdio_mcp across admin, agent, docs
    mcp_admin = get_stdio_mcp(surface="admin")
    assert "Plane MCP Server (stdio)" in mcp_admin.name

    mcp_agent = get_stdio_mcp(surface="agent")
    assert "Plane Agent MCP Server (stdio)" in mcp_agent.name

    mcp_docs = get_stdio_mcp(surface="docs")
    assert "Plane Docs MCP Server (stdio)" in mcp_docs.name
