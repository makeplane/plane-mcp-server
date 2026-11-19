"""Deterministic unit and integration tests for Plane CE list fallbacks.

Covers project and workspace member listing compatibility with self-hosted Plane
Community Edition instances lacking /projects-lite/ and /members-lite/.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from plane.errors import HttpError
from plane.models.projects import (
    PaginatedProjectLiteResponse,
    PaginatedProjectResponse,
    Project,
    ProjectLite,
)
from plane.models.workspaces import (
    PaginatedWorkspaceMemberResponse,
    WorkspaceMember,
)

from plane_mcp.ce_list import (
    _PROJECT_LITE_FIELDS,
    _ce_page,
    list_projects_ce,
    list_workspace_members_ce,
)
from plane_mcp.tools.member import register as register_member
from plane_mcp.tools.project import register as register_project


def _make_http_error(status: int = 404, message: str | None = None) -> HttpError:
    msg = message if message is not None else f"HTTP {status}: Error"
    return HttpError(msg, status_code=status, response={"error": msg})


def _make_project_page(
    results: list[Any],
    *,
    next_page: bool = False,
    next_cursor: str = "",
    prev_cursor: str = "",
    total_count: int | None = None,
) -> PaginatedProjectResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedProjectResponse(
        results=results,
        count=len(results),
        total_count=total,
        total_results=total,
        total_pages=1 if total else 0,
        next_page_results=next_page,
        prev_page_results=bool(prev_cursor),
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )


def _make_project_lite_page(
    results: list[Any],
    *,
    next_page: bool = False,
    next_cursor: str = "",
    prev_cursor: str = "",
    total_count: int | None = None,
) -> PaginatedProjectLiteResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedProjectLiteResponse(
        results=results,
        count=len(results),
        total_count=total,
        total_results=total,
        total_pages=1 if total else 0,
        next_page_results=next_page,
        prev_page_results=bool(prev_cursor),
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )


def _make_member_page(
    results: list[Any],
    *,
    next_page: bool = False,
    next_cursor: str = "",
    prev_cursor: str = "",
    total_count: int | None = None,
) -> PaginatedWorkspaceMemberResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedWorkspaceMemberResponse(
        results=results,
        count=len(results),
        total_count=total,
        total_results=total,
        total_pages=1 if total else 0,
        next_page_results=next_page,
        prev_page_results=bool(prev_cursor),
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
    )


@pytest.fixture
def project_server() -> FastMCP:
    server = FastMCP("test-project-server")
    register_project(server)
    return server


@pytest.fixture
def member_server() -> FastMCP:
    server = FastMCP("test-member-server")
    register_member(server)
    return server


# ============================================================================
# Core Logic Unit Tests: _ce_page & Cursors
# ============================================================================


def test_ce_page_empty_list():
    page = _ce_page([], cursor="", per_page=10, model=PaginatedProjectLiteResponse)
    assert page.results == []
    assert page.count == 0
    assert page.total_count == 0
    assert page.total_results == 0
    assert page.total_pages == 0
    assert page.next_page_results is False
    assert page.prev_page_results is False
    assert page.next_cursor == ""
    assert page.prev_cursor == ""


def test_ce_page_single_page():
    items = [{"id": f"p{i}", "name": f"Project {i}", "identifier": f"P{i}"} for i in range(5)]
    page = _ce_page(items, cursor="", per_page=10, model=PaginatedProjectLiteResponse)
    assert len(page.results) == 5
    assert page.count == 5
    assert page.total_count == 5
    assert page.total_pages == 1
    assert page.next_page_results is False
    assert page.prev_page_results is False
    assert page.next_cursor == ""
    assert page.prev_cursor == ""


def test_ce_page_multi_page_and_continuation():
    items = [{"id": f"p{i}", "name": f"Project {i}", "identifier": f"P{i}"} for i in range(5)]

    # Page 1 (offset 0, per_page 2)
    p1 = _ce_page(items, cursor="", per_page=2, model=PaginatedProjectLiteResponse)
    assert len(p1.results) == 2
    assert p1.results[0].id == "p0"
    assert p1.results[1].id == "p1"
    assert p1.next_page_results is True
    assert p1.next_cursor == "ce:2"
    assert p1.prev_page_results is False
    assert p1.prev_cursor == ""

    # Page 2 (offset 2, per_page 2)
    p2 = _ce_page(items, cursor="ce:2", per_page=2, model=PaginatedProjectLiteResponse)
    assert len(p2.results) == 2
    assert p2.results[0].id == "p2"
    assert p2.results[1].id == "p3"
    assert p2.next_page_results is True
    assert p2.next_cursor == "ce:4"
    assert p2.prev_page_results is True
    assert p2.prev_cursor == "ce:0"

    # Page 3 (offset 4, per_page 2) - last page with 1 item
    p3 = _ce_page(items, cursor="ce:4", per_page=2, model=PaginatedProjectLiteResponse)
    assert len(p3.results) == 1
    assert p3.results[0].id == "p4"
    assert p3.next_page_results is False
    assert p3.next_cursor == ""
    assert p3.prev_page_results is True
    assert p3.prev_cursor == "ce:2"


def test_ce_page_repeated_cursor_is_idempotent():
    items = [{"id": f"p{i}", "name": f"Project {i}", "identifier": f"P{i}"} for i in range(5)]
    res1 = _ce_page(items, cursor="ce:2", per_page=2, model=PaginatedProjectLiteResponse)
    res2 = _ce_page(items, cursor="ce:2", per_page=2, model=PaginatedProjectLiteResponse)
    assert [r.id for r in res1.results] == [r.id for r in res2.results] == ["p2", "p3"]
    assert res1.next_cursor == res2.next_cursor == "ce:4"


def test_ce_page_cursor_validation():
    items = [{"id": "p1", "name": "Project 1", "identifier": "P1"}]

    with pytest.raises(ToolError, match="Invalid CE pagination cursor"):
        _ce_page(items, cursor="invalid", per_page=10, model=PaginatedProjectLiteResponse)

    with pytest.raises(ToolError, match="Invalid CE pagination cursor"):
        _ce_page(items, cursor="ce:-1", per_page=10, model=PaginatedProjectLiteResponse)

    with pytest.raises(ToolError, match="out of range"):
        _ce_page(items, cursor="ce:10", per_page=10, model=PaginatedProjectLiteResponse)

    with pytest.raises(ToolError, match="per_page must be in the range"):
        _ce_page(items, cursor="", per_page=0, model=PaginatedProjectLiteResponse)

    with pytest.raises(ToolError, match="per_page must be in the range"):
        _ce_page(items, cursor="", per_page=1001, model=PaginatedProjectLiteResponse)


def test_ce_page_cursor_truncation_security():
    """Verify that sensitive or oversized cursor values are safely truncated in errors (CE-AUDIT-005)."""
    items = [{"id": "p1", "name": "Project 1", "identifier": "P1"}]
    oversized_secret_cursor = "sk_live_" + "secret_token_value_abc123_" * 50

    with pytest.raises(ToolError) as exc_info:
        _ce_page(items, cursor=oversized_secret_cursor, per_page=10, model=PaginatedProjectLiteResponse)

    msg = str(exc_info.value)
    assert "Invalid CE pagination cursor" in msg
    assert "..." in msg
    # The full secret string must NEVER be reflected
    assert oversized_secret_cursor not in msg
    assert len(msg) < 150


def test_ce_page_cursor_numeric_limits():
    """Verify numeric cursor offset limits, leading zeros, and oversized inputs."""
    items = [
        {"id": "p1", "name": "P1", "identifier": "P1"},
        {"id": "p2", "name": "P2", "identifier": "P2"},
    ]

    # 1. Valid cursor
    res_valid = _ce_page(items, cursor="ce:1", per_page=10, model=PaginatedProjectLiteResponse)
    assert len(res_valid.results) == 1
    assert res_valid.results[0].id == "p2"

    # 2. Leading zeros
    res_lz = _ce_page(items, cursor="ce:001", per_page=10, model=PaginatedProjectLiteResponse)
    assert len(res_lz.results) == 1
    assert res_lz.results[0].id == "p2"

    # 3. Invalid negative offset
    with pytest.raises(ToolError, match="Invalid CE pagination cursor"):
        _ce_page(items, cursor="ce:-1", per_page=10, model=PaginatedProjectLiteResponse)

    # 4. Valid but out-of-range offset within digit limit
    with pytest.raises(ToolError, match="out of range for total 2 items"):
        _ce_page(items, cursor="ce:99999", per_page=10, model=PaginatedProjectLiteResponse)

    # 5. Oversized decimal cursor (>10 digits)
    with pytest.raises(ToolError, match="Offset exceeds maximum supported digits"):
        _ce_page(items, cursor="ce:12345678901", per_page=10, model=PaginatedProjectLiteResponse)

    # 6. More than 4,300 digits (safely rejected with ToolError without triggering ValueError)
    huge_cursor = "ce:" + "9" * 5000
    with pytest.raises(ToolError) as exc_info:
        _ce_page(items, cursor=huge_cursor, per_page=10, model=PaginatedProjectLiteResponse)
    assert "Offset exceeds maximum supported digits" in str(exc_info.value)
    assert "..." in str(exc_info.value)
    assert len(str(exc_info.value)) < 150


# ============================================================================
# Unit Tests for list_projects_ce
# ============================================================================


def test_list_projects_ce_excludes_archived():
    client = MagicMock()
    p_active = Project(id="p1", name="Active", identifier="ACT", archived_at=None, total_members=5)
    p_archived = Project(id="p2", name="Archived", identifier="ARC", archived_at="2026-01-01T00:00:00Z")
    client.projects.list.return_value = _make_project_page([p_active, p_archived])

    page = list_projects_ce(client, "test-ws")
    assert len(page.results) == 1
    assert page.results[0].id == "p1"
    assert page.results[0].name == "Active"
    assert page.total_count == 1


def test_list_projects_ce_trims_to_lite_fields():
    """Ensure extra Project fields (like total_members, network) are not leaked in ProjectLite."""
    client = MagicMock()
    full_project = Project(
        id="p1",
        name="Test",
        identifier="TST",
        archived_at=None,
        total_members=15,
        network=2,
        created_by="user-uuid",
    )
    client.projects.list.return_value = _make_project_page([full_project])

    page = list_projects_ce(client, "test-ws")
    assert len(page.results) == 1
    item = page.results[0]
    assert isinstance(item, ProjectLite)
    assert set(item.model_dump().keys()) == _PROJECT_LITE_FIELDS
    assert not hasattr(item, "total_members") or "total_members" not in item.model_dump()


def test_list_projects_ce_multi_backend_pages():
    client = MagicMock()
    page1 = _make_project_page(
        [Project(id="p1", name="P1", identifier="P1", archived_at=None)],
        next_page=True,
        next_cursor="c2",
        total_count=2,
    )
    page2 = _make_project_page(
        [Project(id="p2", name="P2", identifier="P2", archived_at=None)],
        next_page=False,
        next_cursor="",
        total_count=2,
    )
    client.projects.list.side_effect = [page1, page2]

    res = list_projects_ce(client, "test-ws")
    assert [p.id for p in res.results] == ["p1", "p2"]
    assert res.total_count == 2
    assert client.projects.list.call_count == 2


def test_list_projects_ce_detects_duplicate_ids():
    client = MagicMock()
    p1 = Project(id="p1", name="P1", identifier="P1", archived_at=None)
    client.projects.list.return_value = _make_project_page([p1, p1])
    with pytest.raises(ToolError, match="duplicate project IDs") as exc_info:
        list_projects_ce(client, "test-ws")
    assert "Concurrent workspace changes" in str(exc_info.value)


def test_list_projects_ce_detects_cursor_stall():
    client = MagicMock()
    p1 = Project(id="p1", name="P1", identifier="P1", archived_at=None)
    page = _make_project_page(
        [p1],
        next_page=True,
        next_cursor="",  # Stalled: next_page=True but next_cursor is empty
    )
    client.projects.list.return_value = page
    with pytest.raises(ToolError, match="pagination stalled"):
        list_projects_ce(client, "test-ws")


def test_list_projects_ce_detects_repeated_cursor_stall():
    client = MagicMock()
    p1 = Project(id="p1", name="P1", identifier="P1", archived_at=None)
    p2 = Project(id="p2", name="P2", identifier="P2", archived_at=None)
    page1 = _make_project_page([p1], next_page=True, next_cursor="loop-cursor")
    page2 = _make_project_page([p2], next_page=True, next_cursor="loop-cursor")
    client.projects.list.side_effect = [page1, page2]

    with pytest.raises(ToolError, match="pagination stalled"):
        list_projects_ce(client, "test-ws")


def test_list_projects_ce_malformed_response():
    client = MagicMock()
    client.projects.list.return_value = {"not": "paginated"}
    with pytest.raises(ToolError, match="valid paginated response"):
        list_projects_ce(client, "test-ws")


def test_list_projects_ce_max_projects_limit():
    client = MagicMock()
    fake_projects = [Project(id=f"p{i}", name=f"P{i}", identifier=f"P{i}", archived_at=None) for i in range(101)]
    page = _make_project_page(fake_projects)
    client.projects.list.return_value = page

    with patch("plane_mcp.ce_list._MAX_PROJECTS", 100):
        with pytest.raises(ToolError, match="maximum scan limit"):
            list_projects_ce(client, "test-ws")


def test_list_projects_ce_max_pages_limit():
    client = MagicMock()
    with patch("plane_mcp.ce_list._MAX_PAGES", 3):
        with pytest.raises(ToolError, match="exceeded maximum limit of 3 pages"):
            client.projects.list.side_effect = [
                _make_project_page(
                    [Project(id=f"p{i}", name=f"P{i}", identifier=f"P{i}", archived_at=None)],
                    next_page=True,
                    next_cursor=f"cursor-{i}",
                )
                for i in range(10)
            ]
            list_projects_ce(client, "test-ws")


def test_list_projects_ce_default_bounds_unpatched():
    """Verify that unpatched default limit caps scan at 10 pages / 1000 projects (CE-AUDIT-003)."""
    client = MagicMock()
    # 11 pages of 1 project each with next_page=True should hit default _MAX_PAGES (10)
    client.projects.list.side_effect = [
        _make_project_page(
            [Project(id=f"p{i}", name=f"P{i}", identifier=f"P{i}", archived_at=None)],
            next_page=True,
            next_cursor=f"cursor-{i}",
        )
        for i in range(15)
    ]
    with pytest.raises(ToolError, match="exceeded maximum limit of 10 pages"):
        list_projects_ce(client, "test-ws")


# ============================================================================
# Unit Tests for list_workspace_members_ce
# ============================================================================


def test_list_workspace_members_ce_filtering_and_sorting():
    client = MagicMock()
    m1 = WorkspaceMember(
        id="m1",
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
        display_name="asmith",
        role_slug="admin",
        is_active=True,
        is_bot=False,
    )
    m2 = WorkspaceMember(
        id="m2",
        first_name="Bob",
        last_name="Jones",
        email="bob@example.com",
        display_name="bjones",
        role_slug="member",
        is_active=True,
        is_bot=False,
    )
    m3 = WorkspaceMember(
        id="m3",
        first_name="Charlie",
        last_name="Bot",
        email="bot@example.com",
        display_name="bot",
        role_slug="guest",
        is_active=False,
        is_bot=True,
    )
    client.workspaces.get_members.return_value = [m1, m2, m3]

    # Filter by text (case-insensitive substring)
    res_text = list_workspace_members_ce(client, "test-ws", first_name="ali")
    assert [m.id for m in res_text.results] == ["m1"]

    # Filter by role_slug
    res_role = list_workspace_members_ce(client, "test-ws", role_slug="member")
    assert [m.id for m in res_role.results] == ["m2"]

    # Filter by is_bot
    res_bot = list_workspace_members_ce(client, "test-ws", is_bot=True)
    assert [m.id for m in res_bot.results] == ["m3"]

    # Filter by is_active
    res_active = list_workspace_members_ce(client, "test-ws", is_active=False)
    assert [m.id for m in res_active.results] == ["m3"]

    # Order by first_name ascending
    res_sort_asc = list_workspace_members_ce(client, "test-ws", order_by="first_name")
    assert [m.first_name for m in res_sort_asc.results] == ["Alice", "Bob", "Charlie"]

    # Order by first_name descending
    res_sort_desc = list_workspace_members_ce(client, "test-ws", order_by="-first_name")
    assert [m.first_name for m in res_sort_desc.results] == ["Charlie", "Bob", "Alice"]


def test_list_workspace_members_ce_handles_none_display_name_cleanly():
    """Verify that records with None display_name or text fields do not cause AttributeError."""
    client = MagicMock()
    m1 = WorkspaceMember(id="m1", first_name="Alice", display_name=None, role_slug="admin")
    m2 = WorkspaceMember(id="m2", first_name="Bob", display_name="Bobby", role_slug="member")
    client.workspaces.get_members.return_value = [m1, m2]

    # Filtering by display_name should not crash on None
    res = list_workspace_members_ce(client, "test-ws", display_name="bob")
    assert [m.id for m in res.results] == ["m2"]

    # Sorting by display_name should put None at the end
    res_sort = list_workspace_members_ce(client, "test-ws", order_by="display_name")
    assert [m.id for m in res_sort.results] == ["m2", "m1"]


def test_list_workspace_members_ce_tuple_input_and_immutability():
    """Verify tuple response sorting works without AttributeError and does not mutate caller list (CE-AUDIT-001)."""
    client = MagicMock()
    m1 = WorkspaceMember(id="m1", first_name="Bob")
    m2 = WorkspaceMember(id="m2", first_name="Alice")

    # 1. Tuple input sorting
    client.workspaces.get_members.return_value = (m1, m2)
    res = list_workspace_members_ce(client, "test-ws", order_by="first_name")
    assert [m.id for m in res.results] == ["m2", "m1"]

    # 2. Immutability of caller-owned list
    original_list = [m1, m2]
    client.workspaces.get_members.return_value = original_list
    res2 = list_workspace_members_ce(client, "test-ws", order_by="first_name")
    assert [m.id for m in res2.results] == ["m2", "m1"]
    # The original list passed to or returned by the client must remain untouched
    assert original_list == [m1, m2]


def test_list_workspace_members_ce_nullable_and_missing_field_matrix():
    """Verify the 4-case matrix for nullable vs missing member fields (CE-AUDIT-002).

    Case A: Explicitly present field with null value (legitimate non-match, does not fail).
    Case B: Entirely missing field in an untyped dictionary (fails closed with ToolError).
    Case C: Pydantic model field with default None when raw response omitted the field (fails closed).
    Case D: API schema omission across records (fails closed).
    """
    client = MagicMock()

    # Case A: Explicitly supplied null value
    # Server returned: {"id": "m1", "first_name": "Alice", "role_slug": None, "is_bot": None}
    m_case_a = WorkspaceMember.model_validate({"id": "m1", "first_name": "Alice", "role_slug": None, "is_bot": None})
    m_active = WorkspaceMember.model_validate(
        {"id": "m2", "first_name": "Admin", "role_slug": "admin", "is_bot": False}
    )
    client.workspaces.get_members.return_value = [m_case_a, m_active]

    # Explicit null should evaluate as non-match without error, returning the matching member
    res_role = list_workspace_members_ce(client, "test-ws", role_slug="admin")
    assert [m.id for m in res_role.results] == ["m2"]

    res_bot = list_workspace_members_ce(client, "test-ws", is_bot=False)
    assert [m.id for m in res_bot.results] == ["m2"]

    # Querying for role_slug="guest" when records are only None and "admin" yields empty results without error
    res_none = list_workspace_members_ce(client, "test-ws", role_slug="guest")
    assert res_none.results == []

    # Case B: Entirely missing field in an untyped dictionary
    client.workspaces.get_members.return_value = [{"id": "d1", "first_name": "DictUser"}]
    with pytest.raises(ToolError, match="cannot reliably filter by role_slug"):
        list_workspace_members_ce(client, "test-ws", role_slug="admin")

    with pytest.raises(ToolError, match="cannot reliably filter by is_bot"):
        list_workspace_members_ce(client, "test-ws", is_bot=False)

    # Case C: Pydantic model where field was omitted from raw payload
    m_omitted = WorkspaceMember(id="m_omit", first_name="OmittedFields")
    client.workspaces.get_members.return_value = [m_omitted]
    with pytest.raises(ToolError, match="cannot reliably filter by is_bot"):
        list_workspace_members_ce(client, "test-ws", is_bot=False)

    with pytest.raises(ToolError, match="cannot reliably filter by is_active"):
        list_workspace_members_ce(client, "test-ws", is_active=True)

    # Case D: Empty collection preserves empty-collection semantics without error
    client.workspaces.get_members.return_value = []
    res_empty = list_workspace_members_ce(client, "test-ws", role_slug="admin", is_active=True, is_bot=False)
    assert res_empty.results == []


def test_list_workspace_members_ce_text_filters_presence_and_matrix():
    """Verify presence checking, omitted field fail-closed, null handling, and matches across all 4 text filters."""
    client = MagicMock()
    text_fields = ["first_name", "last_name", "email", "display_name"]

    # 1. Omitted Pydantic field: fails closed with ToolError for all 4 text fields
    m_omitted = WorkspaceMember(id="m_omit")
    client.workspaces.get_members.return_value = [m_omitted]
    for field in text_fields:
        with pytest.raises(ToolError, match=f"cannot reliably filter by {field}"):
            list_workspace_members_ce(client, "test-ws", **{field: "test"})

    # 2. Missing dictionary key: fails closed with ToolError for all 4 text fields
    client.workspaces.get_members.return_value = [{"id": "d1"}]
    for field in text_fields:
        with pytest.raises(ToolError, match=f"cannot reliably filter by {field}"):
            list_workspace_members_ce(client, "test-ws", **{field: "test"})

    # 3. Explicit None: non-matching, does not raise error
    m_null = WorkspaceMember.model_validate(
        {
            "id": "m_null",
            "first_name": None,
            "last_name": None,
            "email": None,
            "display_name": None,
        }
    )
    m_target = WorkspaceMember.model_validate(
        {
            "id": "m_target",
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "display_name": "AliceS",
        }
    )
    client.workspaces.get_members.return_value = [m_null, m_target]
    for field, needle in [
        ("first_name", "alice"),
        ("last_name", "smith"),
        ("email", "example.com"),
        ("display_name", "alices"),
    ]:
        res = list_workspace_members_ce(client, "test-ws", **{field: needle})
        assert [m.id for m in res.results] == ["m_target"]

    # Explicit None records queried with a non-matching string yield empty results cleanly
    res_none = list_workspace_members_ce(client, "test-ws", first_name="nonexistent")
    assert res_none.results == []

    # 4. Mixed records (one present, one omitted): fails closed with ToolError
    client.workspaces.get_members.return_value = [m_target, m_omitted]
    for field in text_fields:
        with pytest.raises(ToolError, match=f"cannot reliably filter by {field}"):
            list_workspace_members_ce(client, "test-ws", **{field: "test"})

    # 5. Unicode case folding & substring matching
    m_unicode = WorkspaceMember.model_validate(
        {
            "id": "m_uni",
            "first_name": "STRAßE",
            "last_name": "MÜLLER",
            "email": "USER@DOMAIN.COM",
            "display_name": "ÉLÈVE",
        }
    )
    client.workspaces.get_members.return_value = [m_unicode]
    assert len(list_workspace_members_ce(client, "test-ws", first_name="straße").results) == 1
    assert len(list_workspace_members_ce(client, "test-ws", last_name="müller").results) == 1
    assert len(list_workspace_members_ce(client, "test-ws", email="user@domain").results) == 1
    assert len(list_workspace_members_ce(client, "test-ws", display_name="élève").results) == 1

    # 6. Empty collection returns empty list without error
    client.workspaces.get_members.return_value = []
    for field in text_fields:
        res_empty = list_workspace_members_ce(client, "test-ws", **{field: "test"})
        assert res_empty.results == []


def test_list_workspace_members_ce_descending_sort_stability():
    """Verify null-last placement and deterministic tie-breaking in ASC and DESC (CE-AUDIT-004)."""
    client = MagicMock()
    m_null = WorkspaceMember(id="m_null", first_name=None)
    m_alice1 = WorkspaceMember(id="m_alice_1", first_name="Alice")
    m_alice2 = WorkspaceMember(id="m_alice_2", first_name="Alice")
    m_bob = WorkspaceMember(id="m_bob", first_name="Bob")

    client.workspaces.get_members.return_value = [m_null, m_alice2, m_bob, m_alice1]

    # Ascending: Alice (m_alice_1, m_alice_2), Bob (m_bob), Null (m_null at the end)
    res_asc = list_workspace_members_ce(client, "test-ws", order_by="first_name")
    assert [m.id for m in res_asc.results] == ["m_alice_1", "m_alice_2", "m_bob", "m_null"]

    # Descending: Bob (m_bob), Alice (m_alice_1, m_alice_2), Null (m_null ALWAYS at the end!)
    res_desc = list_workspace_members_ce(client, "test-ws", order_by="-first_name")
    assert [m.id for m in res_desc.results] == ["m_bob", "m_alice_1", "m_alice_2", "m_null"]
    # Deterministic tie-breaker check: m_alice_1 (< m_alice_2) comes first in both ASC and DESC
    alice_ids = [m.id for m in res_desc.results if m.first_name == "Alice"]
    assert alice_ids == ["m_alice_1", "m_alice_2"]


def test_list_workspace_members_ce_unsupported_order_by():
    client = MagicMock()
    m1 = WorkspaceMember(id="m1", first_name="Alice")
    client.workspaces.get_members.return_value = [m1]

    with pytest.raises(ToolError, match="Unsupported CE workspace member order_by: 'created_at'"):
        list_workspace_members_ce(client, "test-ws", order_by="created_at")


def test_list_workspace_members_ce_malformed_and_limit():
    client = MagicMock()
    client.workspaces.get_members.return_value = "not-a-list"
    with pytest.raises(ToolError, match="valid list"):
        list_workspace_members_ce(client, "test-ws")

    client.workspaces.get_members.return_value = [WorkspaceMember(id=f"m{i}") for i in range(105)]
    with patch("plane_mcp.ce_list._MAX_MEMBERS", 100):
        with pytest.raises(ToolError, match="maximum limit"):
            list_workspace_members_ce(client, "test-ws")


# ============================================================================
# Dispatch Tests: Project Tool
# ============================================================================


@pytest.mark.anyio
async def test_tool_project_list_cloud_success(project_server: FastMCP):
    """Normal Cloud endpoint succeeds directly without invoking CE fallback."""
    client = MagicMock()
    client.projects.list_lite.return_value = _make_project_lite_page(
        [ProjectLite(id="p-cloud", name="Cloud Project", identifier="CLD")]
    )

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(project_server) as mcp_client:
            res = await mcp_client.call_tool("project", {"action": "list"})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            pid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert pid == "p-cloud"
            client.projects.list_lite.assert_called_once()
            client.projects.list.assert_not_called()


@pytest.mark.anyio
async def test_tool_project_list_falls_back_on_404(project_server: FastMCP):
    """When list_lite returns 404, project tool falls back to CE list."""
    client = MagicMock()
    client.projects.list_lite.side_effect = _make_http_error(404, "Page not found.")
    client.projects.list.return_value = _make_project_page(
        [Project(id="p-ce", name="CE Project", identifier="CE", archived_at=None)]
    )

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(project_server) as mcp_client:
            res = await mcp_client.call_tool("project", {"action": "list"})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            pid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert pid == "p-ce"
            client.projects.list_lite.assert_called_once()
            client.projects.list.assert_called_once()


@pytest.mark.anyio
async def test_tool_project_list_propagates_non_404_errors(project_server: FastMCP):
    """401, 403, 429, 500 from list_lite are never downgraded or caught as fallback."""
    for code in (401, 403, 429, 500):
        client = MagicMock()
        client.projects.list_lite.side_effect = _make_http_error(code)

        with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
            async with Client(project_server) as mcp_client:
                res = await mcp_client.call_tool_mcp("project", {"action": "list"})
                assert res.isError is True
                assert f"HTTP {code}" in res.content[0].text
                client.projects.list.assert_not_called()


@pytest.mark.anyio
async def test_tool_project_list_propagates_network_failure(project_server: FastMCP):
    client = MagicMock()
    client.projects.list_lite.side_effect = ConnectionError("Failed to resolve host")

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(project_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("project", {"action": "list"})
            assert res.isError is True
            assert "Failed to resolve host" in res.content[0].text
            client.projects.list.assert_not_called()


@pytest.mark.anyio
async def test_tool_project_list_both_endpoints_404_propagates(project_server: FastMCP):
    """When both list_lite and list return 404 (e.g. workspace missing), 404 is propagated."""
    client = MagicMock()
    client.projects.list_lite.side_effect = _make_http_error(404, "HTTP 404: Lite not found")
    client.projects.list.side_effect = _make_http_error(404, "HTTP 404: Workspace not found")

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "nonexistent-ws")):
        async with Client(project_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("project", {"action": "list"})
            assert res.isError is True
            assert "404" in res.content[0].text
            client.projects.list_lite.assert_called_once()
            client.projects.list.assert_called_once()


# ============================================================================
# Dispatch Tests: Member Tool
# ============================================================================


@pytest.mark.anyio
async def test_tool_member_list_cloud_success(member_server: FastMCP):
    """Normal Cloud endpoint succeeds directly without invoking CE fallback."""
    client = MagicMock()
    client.workspaces.get_members_lite.return_value = _make_member_page(
        [WorkspaceMember(id="m-cloud", first_name="Cloud", last_name="Member")]
    )

    with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(member_server) as mcp_client:
            res = await mcp_client.call_tool("member", {"action": "list_workspace"})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            mid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert mid == "m-cloud"
            client.workspaces.get_members_lite.assert_called_once()
            client.workspaces.get_members.assert_not_called()


@pytest.mark.anyio
async def test_tool_member_list_falls_back_on_404(member_server: FastMCP):
    """When get_members_lite returns 404, member tool falls back to CE list."""
    client = MagicMock()
    client.workspaces.get_members_lite.side_effect = _make_http_error(404, "Page not found.")
    client.workspaces.get_members.return_value = [
        WorkspaceMember(id="m-ce", first_name="CE", last_name="User", role_slug="admin")
    ]

    with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(member_server) as mcp_client:
            res = await mcp_client.call_tool("member", {"action": "list_workspace"})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            mid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert mid == "m-ce"
            client.workspaces.get_members_lite.assert_called_once()
            client.workspaces.get_members.assert_called_once()


@pytest.mark.anyio
async def test_tool_member_list_propagates_non_404_errors(member_server: FastMCP):
    """401, 403, 429, 500 from get_members_lite are never downgraded or caught as fallback."""
    for code in (401, 403, 429, 500):
        client = MagicMock()
        client.workspaces.get_members_lite.side_effect = _make_http_error(code)

        with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "test-ws")):
            async with Client(member_server) as mcp_client:
                res = await mcp_client.call_tool_mcp("member", {"action": "list_workspace"})
                assert res.isError is True
                assert f"HTTP {code}" in res.content[0].text
                client.workspaces.get_members.assert_not_called()


@pytest.mark.anyio
async def test_tool_member_list_both_endpoints_404_propagates(member_server: FastMCP):
    client = MagicMock()
    client.workspaces.get_members_lite.side_effect = _make_http_error(404, "HTTP 404: Lite not found")
    client.workspaces.get_members.side_effect = _make_http_error(404, "HTTP 404: Workspace not found")

    with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "nonexistent-ws")):
        async with Client(member_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("member", {"action": "list_workspace"})
            assert res.isError is True
            assert "404" in res.content[0].text
            client.workspaces.get_members_lite.assert_called_once()
            client.workspaces.get_members.assert_called_once()


# ============================================================================
# Raw MCP Protocol & Error Semantics Tests (isError=true)
# ============================================================================


@pytest.mark.anyio
async def test_raw_mcp_is_error_project_invalid_cursor(project_server: FastMCP):
    client = MagicMock()
    client.projects.list_lite.side_effect = _make_http_error(404, "Page not found.")
    client.projects.list.return_value = _make_project_page(
        [Project(id="p1", name="P1", identifier="P1", archived_at=None)]
    )

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(project_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("project", {"action": "list", "cursor": "invalid_cursor"})
            assert res.isError is True
            assert "Invalid CE pagination cursor" in res.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_is_error_project_pagination_stall(project_server: FastMCP):
    client = MagicMock()
    client.projects.list_lite.side_effect = _make_http_error(404, "Page not found.")
    client.projects.list.return_value = _make_project_page(
        [Project(id="p1", name="P1", identifier="P1", archived_at=None)],
        next_page=True,
        next_cursor="",
    )

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(project_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("project", {"action": "list"})
            assert res.isError is True
            assert "pagination stalled" in res.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_is_error_member_unsupported_order_by(member_server: FastMCP):
    client = MagicMock()
    client.workspaces.get_members_lite.side_effect = _make_http_error(404, "Page not found.")
    client.workspaces.get_members.return_value = [WorkspaceMember(id="m1", first_name="A")]

    with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(member_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("member", {"action": "list_workspace", "order_by": "unsupported"})
            assert res.isError is True
            assert "Unsupported CE workspace member order_by" in res.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_is_error_member_unavailable_filter_field(member_server: FastMCP):
    client = MagicMock()
    client.workspaces.get_members_lite.side_effect = _make_http_error(404, "Page not found.")
    client.workspaces.get_members.return_value = [WorkspaceMember(id="m1", first_name="A")]

    with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(member_server) as mcp_client:
            res = await mcp_client.call_tool_mcp("member", {"action": "list_workspace", "is_active": True})
            assert res.isError is True
            assert "cannot reliably filter by is_active" in res.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_is_error_member_unavailable_text_filter_field(member_server: FastMCP):
    """Verify raw MCP isError=true when member text filter fields are omitted from records."""
    client = MagicMock()
    client.workspaces.get_members_lite.side_effect = _make_http_error(404, "Page not found.")
    # Member with omitted email and display_name
    client.workspaces.get_members.return_value = [WorkspaceMember(id="m1", first_name="Alice")]

    with patch("plane_mcp.tools.member.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(member_server) as mcp_client:
            for field in ("email", "last_name", "display_name"):
                res = await mcp_client.call_tool_mcp("member", {"action": "list_workspace", field: "query"})
                assert res.isError is True
                assert f"cannot reliably filter by {field}" in res.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_is_error_project_oversized_numeric_cursor(project_server: FastMCP):
    """Verify raw MCP isError=true for oversized and >4,300 digit cursor offsets."""
    client = MagicMock()
    client.projects.list_lite.side_effect = _make_http_error(404, "Page not found.")
    client.projects.list.return_value = _make_project_page(
        [Project(id="p1", name="P1", identifier="P1", archived_at=None)]
    )

    with patch("plane_mcp.tools.project.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(project_server) as mcp_client:
            # 1. 11-digit cursor
            res1 = await mcp_client.call_tool_mcp("project", {"action": "list", "cursor": "ce:12345678901"})
            assert res1.isError is True
            assert "Offset exceeds maximum supported digits" in res1.content[0].text

            # 2. >4,300 digit cursor
            res2 = await mcp_client.call_tool_mcp("project", {"action": "list", "cursor": "ce:" + "9" * 5000})
            assert res2.isError is True
            assert "Offset exceeds maximum supported digits" in res2.content[0].text
