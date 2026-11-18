"""Exhaustive tests for 100% coverage on resolver, docs lod & tools, and sanitize."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from plane_mcp.docs.tools import register_docs_tools
from plane_mcp.resolver import EntityResolver, is_valid_uuid, validate_identifier
from plane_mcp.sanitize import (
    apply_snippet_edits,
    html_to_clean_markdown,
    markdown_or_html_to_safe_html,
    sanitize_html,
)


def test_validate_identifier_and_uuid_branches():
    """Verify UUID validation and identifier type and path traversal branches."""
    assert is_valid_uuid("11111111-1111-1111-1111-111111111111")
    assert not is_valid_uuid("not-a-uuid")
    assert not is_valid_uuid(None)

    with pytest.raises(ValueError, match="must be a string"):
        validate_identifier(12345, "field")

    # Path traversal with backslash
    with pytest.raises(ValueError, match="path traversal sequence"):
        validate_identifier("..\\windows\\path", "field")


def test_sanitize_html_edge_cases():
    """Verify sanitize_html, markdown_or_html_to_safe_html, and html_to_clean_markdown edge cases."""
    assert sanitize_html(None) is None
    assert sanitize_html("") == ""

    # markdown_or_html_to_safe_html branches
    assert markdown_or_html_to_safe_html(None) is None
    assert markdown_or_html_to_safe_html("   ") == ""
    # HTML tag branch
    res_html = markdown_or_html_to_safe_html("<div>Hello World</div>")
    assert "<div>Hello World</div>" in res_html
    # Markdown paragraph branch
    res_md = markdown_or_html_to_safe_html("Para 1\nLine 2\n\nPara 2")
    assert "<p>Para 1<br>Line 2</p><p>Para 2</p>" in res_md

    # html_to_clean_markdown branches
    assert html_to_clean_markdown(None) == ""
    assert html_to_clean_markdown("") == ""
    assert html_to_clean_markdown("<h2>Header 2</h2>") == "## Header 2"


def test_apply_snippet_edits_exhaustive():
    """Verify all branches of apply_snippet_edits including entities, anchors, and rejections."""
    current = "<p>Title</p><p>Section A</p>"

    # Neither replace nor append
    out, err = apply_snippet_edits(current)
    assert err is None
    assert out == current

    # Replace without text
    out, err = apply_snippet_edits(current, replace_target_snippet="Title")
    assert err and "replace_text is required" in err

    # Replace with entity match
    entity_html = "<p>Fish &amp; Chips</p>"
    out, err = apply_snippet_edits(entity_html, replace_target_snippet="Fish &amp; Chips", replace_text="Burgers")
    assert err is None
    assert "Burgers" in out

    # Append after snippet
    out, err = apply_snippet_edits(entity_html, append_after_snippet="Fish &amp; Chips", append_text=" and Beer")
    assert err is None
    assert "Beer" in out

    # Append after snippet not found
    out, err = apply_snippet_edits(current, append_after_snippet="NonExistent", append_text="Extra")
    assert err and "Anchor snippet not found" in err

    # Ambiguous anchor snippet
    dup_html = "<p>Dup</p><p>Dup</p>"
    out, err = apply_snippet_edits(dup_html, append_after_snippet="Dup", append_text="Extra")
    assert err and "Ambiguous anchor snippet" in err

    # Whitespace append anchor
    out, err = apply_snippet_edits(current, append_after_snippet="   ", append_text="Extra")
    assert err and "cannot be empty or whitespace only" in err


def test_resolver_exhaustive_branches():
    """Verify EntityResolver cache bounding, UUID passthrough, and entity resolution branches."""
    mock_client = MagicMock()
    resolver = EntityResolver(mock_client, "test-ws")

    # Bound cache trigger
    resolver._bound_cache({f"k{i}": i for i in range(2100)})

    # Resolve project UUID branch
    p_uuid = "11111111-1111-1111-1111-111111111111"
    assert resolver.resolve_project(p_uuid) == p_uuid

    # Resolve project slug match
    proj_slug_mock = MagicMock(identifier="ENG", slug="eng-team")
    proj_slug_mock.name = "Engineering"
    proj_slug_mock.id = p_uuid
    mock_client.projects.list.return_value = MagicMock(results=[proj_slug_mock])
    assert resolver.resolve_project("eng-team") == p_uuid

    # Resolve state UUID and cached branch
    s_uuid = "22222222-2222-2222-2222-222222222222"
    assert resolver.resolve_state("ENG", s_uuid) == s_uuid

    state_mock = MagicMock(name="In Progress", id=s_uuid)
    state_mock.name = "In Progress"
    mock_client.states.list.return_value = MagicMock(results=[state_mock])
    assert resolver.resolve_state("ENG", "In Progress") == s_uuid
    # Cache hit
    assert resolver.resolve_state("ENG", "In Progress") == s_uuid

    # Resolve ticket UUID and errors
    t_uuid = "33333333-3333-3333-3333-333333333333"
    assert resolver.resolve_ticket(t_uuid) == t_uuid

    with pytest.raises(ValueError, match="Invalid ticket ID format"):
        resolver.resolve_ticket("NO_HYPHEN")

    with pytest.raises(ValueError, match="Invalid ticket sequence"):
        resolver.resolve_ticket("ENG-0")

    with pytest.raises(ValueError, match="Invalid ticket sequence"):
        resolver.resolve_ticket("ENG-abc")

    # Ticket found
    mock_client.work_items._get.return_value = {"id": t_uuid}
    assert resolver.resolve_ticket("ENG-101") == t_uuid
    # Cache hit
    assert resolver.resolve_ticket("ENG-101") == t_uuid

    # Resolve collection UUID and name
    c_uuid = "44444444-4444-4444-4444-444444444444"
    assert resolver.resolve_collection(c_uuid) == c_uuid
    coll_mock = MagicMock(id=c_uuid)
    coll_mock.name = "Wiki"
    mock_client.collections.list.return_value = MagicMock(results=[coll_mock])
    assert resolver.resolve_collection("Wiki") == c_uuid
    assert resolver.resolve_collection("Wiki") == c_uuid

    # Resolve template UUID and name
    tmpl_uuid = "55555555-5555-5555-5555-555555555555"
    assert resolver.resolve_template(tmpl_uuid) == tmpl_uuid
    t_mock = MagicMock(id=tmpl_uuid)
    t_mock.name = "RFC"
    mock_client.workspace_templates.pages.list.return_value = MagicMock(results=[t_mock])
    assert resolver.resolve_template("RFC") == tmpl_uuid
    assert resolver.resolve_template("RFC") == tmpl_uuid


@patch("plane_mcp.docs.tools.get_plane_client_context")
def test_docs_tools_exhaustive_actions(mock_get_ctx):
    """Verify search_docs, link_doc, organize_docs, and update_doc action branches."""
    mock_client = MagicMock()
    mock_get_ctx.return_value = (mock_client, "test-ws")

    mcp = FastMCP("test-docs")
    register_docs_tools(mcp)

    # Setup project mock
    p_eng = MagicMock(identifier="ENG", id="11111111-1111-1111-1111-111111111111")
    p_eng.name = "Engineering"
    p_eng.slug = "eng"
    mock_client.projects.list.return_value = MagicMock(results=[p_eng])

    # 1. search_docs with ticket_id
    ticket_uuid = "33333333-3333-3333-3333-333333333333"
    mock_client.work_items._get.return_value = {"id": ticket_uuid}
    link_item = MagicMock(page_id="p-1")
    mock_client.work_items.pages.list.return_value = MagicMock(results=[link_item])
    p_obj = MagicMock(id="p-1", description_html="<p>Doc</p>")
    p_obj.name = "Spec"
    p_obj.parent_id = None
    p_obj.collection_id = None
    p_obj.is_locked = False
    p_obj.updated_at = "now"
    mock_client.pages.retrieve_project_page.return_value = p_obj

    search_fn = asyncio.run(mcp.get_tool("search_docs")).fn
    res_ticket = search_fn(ticket_id="ENG-1")
    assert "Spec" in str(res_ticket)

    # 2. search_docs with collection
    coll_uuid = "44444444-4444-4444-4444-444444444444"
    coll_mock_item = MagicMock(id=coll_uuid)
    coll_mock_item.name = "EngWiki"
    mock_client.collections.list.return_value = MagicMock(results=[coll_mock_item])
    mock_client.collections.pages.list.return_value = MagicMock(results=[p_obj])
    res_coll = search_fn(collection="EngWiki")
    assert "Spec" in str(res_coll)

    # 3. search_docs with templates
    t_obj = MagicMock(id="t-1")
    t_obj.name = "Template A"
    mock_client.workspace_templates.pages.list.return_value = MagicMock(results=[t_obj])
    res_tmpl = search_fn(include_templates=True)
    assert "Template A" in res_tmpl

    # 4. link_doc attach, list, detach
    link_fn = asyncio.run(mcp.get_tool("link_doc")).fn
    res_attach = link_fn(ticket_id="ENG-1", page="11111111-1111-1111-1111-111111111111", action="attach")
    assert "attached" in res_attach

    res_list = link_fn(ticket_id="ENG-1", action="list")
    assert "linked_pages" in res_list

    m_link = MagicMock(id="link-1", page_id="11111111-1111-1111-1111-111111111111")
    mock_client.work_items.pages.list.return_value = MagicMock(results=[m_link])
    res_detach = link_fn(ticket_id="ENG-1", page="11111111-1111-1111-1111-111111111111", action="detach")
    assert "detached" in res_detach

    # 5. organize_docs collections & templates
    org_fn = asyncio.run(mcp.get_tool("organize_docs")).fn
    res_clist = org_fn(target="collection", action="list")
    assert "collections" in res_clist

    c_uuid_new = "66666666-6666-6666-6666-666666666666"
    coll_created = MagicMock(id=c_uuid_new)
    coll_created.name = "CreatedColl"
    mock_client.collections.create.return_value = coll_created
    res_ccreate = org_fn(target="collection", action="create", name_or_id="CreatedColl")
    assert "created" in res_ccreate

    pages_to_add = ["11111111-1111-1111-1111-111111111111"]
    res_cadd = org_fn(target="collection", action="add_pages", name_or_id=c_uuid_new, pages=pages_to_add)
    assert "pages_added" in res_cadd

    res_cupdate = org_fn(target="collection", action="update", name_or_id=c_uuid_new, new_name="UpdatedColl")
    assert "updated" in res_cupdate

    res_cdelete = org_fn(target="collection", action="delete", name_or_id=c_uuid_new)
    assert "deleted" in res_cdelete

    res_tlist = org_fn(target="template", action="list")
    assert "templates" in res_tlist

    res_tdelete = org_fn(target="template", action="delete", name_or_id="55555555-5555-5555-5555-555555555555")
    assert "deleted" in res_tdelete

    # 6. update_doc archive and delete_permanently
    update_fn = asyncio.run(mcp.get_tool("update_doc")).fn
    p_uuid = "11111111-1111-1111-1111-111111111111"
    res_arch = update_fn(page=p_uuid, archive=True)
    assert "archived" in res_arch

    res_del = update_fn(page=p_uuid, delete_permanently=True)
    assert "deleted" in res_del
