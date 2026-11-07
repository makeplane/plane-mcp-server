"""Unit tests for EntityResolver project pagination, cache eviction, ticket context, and per-page TTL."""

from unittest.mock import MagicMock

import pytest
from plane.errors.errors import HttpError
from plane.models.query_params import PaginatedQueryParams

import plane_mcp.resolver
from plane_mcp.resolver import EntityResolutionError, EntityResolver, _extract_template_body


@pytest.fixture
def mock_client():
    """Return a fresh MagicMock PlaneClient instance."""
    return MagicMock()


@pytest.fixture
def resolver(mock_client):
    """Return an EntityResolver bound to test-workspace."""
    return EntityResolver(mock_client, "test-workspace")


def test_resolve_project_success(resolver, mock_client):
    """Verify resolving a project by identifier and slug caches the project mapping."""
    project_mock1 = MagicMock()
    project_mock1.id = "proj-1"
    project_mock1.identifier = "ENG"
    project_mock1.name = "Engineering"
    project_mock1.slug = "engineering"

    mock_response = MagicMock()
    mock_response.results = [project_mock1]
    mock_client.projects.list.return_value = mock_response

    assert resolver.resolve_project("ENG") == "proj-1"
    assert resolver.resolve_project("engineering") == "proj-1"
    assert mock_client.projects.list.call_count == 1


def test_resolve_project_not_found(resolver, mock_client):
    """Verify resolving a missing project raises EntityResolutionError with available options."""
    mock_response = MagicMock()
    mock_response.results = []
    mock_client.projects.list.return_value = mock_response

    with pytest.raises(EntityResolutionError) as exc_info:
        resolver.resolve_project("NOTFOUND")

    assert "Project 'NOTFOUND' not found" in str(exc_info.value)
    assert exc_info.value.available_options == []


def test_resolve_project_follows_pagination_cursors(resolver, mock_client):
    """Verify resolve_project follows next_cursor across multiple pages before resolving."""
    p1 = MagicMock(id="proj-1", identifier="ENG", slug="eng")
    p1.name = "Engineering"
    p2 = MagicMock(id="proj-2", identifier="OPS", slug="ops")
    p2.name = "Operations"

    page1 = MagicMock(results=[p1], next_cursor="100:1:0", next_page_results=True)
    page2 = MagicMock(results=[p2], next_cursor="100:2:0", next_page_results=False)
    mock_client.projects.list.side_effect = [page1, page2]

    assert resolver.resolve_project("OPS") == "proj-2"
    assert mock_client.projects.list.call_count == 2
    assert mock_client.projects.list.call_args_list[0].kwargs == {
        "workspace_slug": "test-workspace",
        "params": PaginatedQueryParams(per_page=100),
    }
    assert mock_client.projects.list.call_args_list[1].kwargs == {
        "workspace_slug": "test-workspace",
        "params": PaginatedQueryParams(per_page=100, cursor="100:1:0"),
    }


def test_resolve_project_evicts_stale_aliases_on_refresh(resolver, mock_client):
    """Verify refreshing the project cache removes renamed or deleted project aliases."""
    old_proj = MagicMock(id="proj-1", identifier="OLD", slug="old-slug")
    old_proj.name = "Old Name"
    mock_client.projects.list.return_value = MagicMock(results=[old_proj], next_cursor=None, next_page_results=False)

    assert resolver.resolve_project("OLD") == "proj-1"
    assert resolver.resolve_project("old-slug") == "proj-1"

    # Expire project cache TTL and return renamed project
    resolver._ts["projects"] = 0.0
    renamed_proj = MagicMock(id="proj-1", identifier="NEW", slug="new-slug")
    renamed_proj.name = "New Name"
    mock_client.projects.list.return_value = MagicMock(
        results=[renamed_proj], next_cursor=None, next_page_results=False
    )

    assert resolver.resolve_project("NEW") == "proj-1"
    assert "OLD" not in resolver._project_cache
    assert "OLD-SLUG" not in resolver._project_cache
    assert "OLD NAME" not in resolver._project_cache

    with pytest.raises(EntityResolutionError):
        resolver.resolve_project("OLD")


def test_resolve_project_identifiers_take_precedence_over_names_and_slugs(resolver, mock_client):
    """Verify canonical project identifiers are not overwritten by later projects' names or slugs."""
    proj_a = MagicMock(id="proj-a", identifier="OPS", slug="operations")
    proj_a.name = "Operations"
    proj_b = MagicMock(id="proj-b", identifier="OPS2", slug="ops")
    proj_b.name = "Ops"

    mock_client.projects.list.return_value = MagicMock(
        results=[proj_a, proj_b], next_cursor=None, next_page_results=False
    )

    assert resolver.resolve_project("OPS") == "proj-a"
    assert resolver.resolve_project("OPS2") == "proj-b"


def test_bound_cache_evicts_per_entry_timestamps(resolver, monkeypatch):
    """Verify _bound_cache removes per-entry timestamps when evicting from page or ticket_context caches."""
    monkeypatch.setattr(plane_mcp.resolver, "_MAX_CACHE_ENTRIES", 4)

    for i in range(5):
        key = f"workspace:page-{i}"
        resolver._page_cache[key] = (f"id-{i}", None)
        resolver._ts[f"page:{key}"] = 1000.0 + i

    resolver._bound_cache(resolver._page_cache, ts_prefix="page")

    assert "workspace:page-0" not in resolver._page_cache
    assert "page:workspace:page-0" not in resolver._ts
    assert "workspace:page-4" in resolver._page_cache
    assert "page:workspace:page-4" in resolver._ts


def test_resolve_state_success(resolver, mock_client):
    """Verify resolving workflow states by case-insensitive name and caching the result."""
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    project_mock.name = "Engineering"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    state_mock1 = MagicMock()
    state_mock1.id = "state-1"
    state_mock1.name = "In Progress"
    state_mock2 = MagicMock()
    state_mock2.id = "state-2"
    state_mock2.name = "Done"

    mock_state_resp = MagicMock()
    mock_state_resp.results = [state_mock1, state_mock2]
    mock_client.states.list.return_value = mock_state_resp

    assert resolver.resolve_state("ENG", "In Progress") == "state-1"
    assert resolver.resolve_state("ENG", "done") == "state-2"
    assert mock_client.states.list.call_count == 1


def test_resolve_state_not_found(resolver, mock_client):
    """Verify resolving an unknown workflow state raises EntityResolutionError."""
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    project_mock.name = "Engineering"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    state_mock1 = MagicMock()
    state_mock1.id = "state-1"
    state_mock1.name = "In Progress"

    mock_state_resp = MagicMock()
    mock_state_resp.results = [state_mock1]
    mock_client.states.list.return_value = mock_state_resp

    with pytest.raises(EntityResolutionError) as exc_info:
        resolver.resolve_state("ENG", "QA")

    assert "State 'QA' not found" in str(exc_info.value)
    assert "In Progress" in exc_info.value.available_options


def test_resolve_ticket_success(resolver, mock_client):
    """Verify resolving a sequence ticket ID (ENG-123) fetches and caches the work item UUID."""
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    mock_client.work_items._get.return_value = {"id": "ticket-1"}

    assert resolver.resolve_ticket("ENG-123") == "ticket-1"
    mock_client.work_items._get.assert_called_once_with("test-workspace/work-items/ENG-123")

    assert resolver.resolve_ticket("eng-123") == "ticket-1"
    assert mock_client.work_items._get.call_count == 1


def test_resolve_ticket_invalid_format(resolver):
    """Verify malformed ticket identifiers raise ValueError."""
    with pytest.raises(ValueError, match="Invalid ticket ID format"):
        resolver.resolve_ticket("ENG123")

    with pytest.raises(ValueError, match="Invalid ticket sequence"):
        resolver.resolve_ticket("ENG-ABC")


def test_resolve_ticket_not_found(resolver, mock_client):
    """Verify HTTP 404 when resolving a ticket raises EntityResolutionError."""
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    class MockHttpError(HttpError):
        def __init__(self):
            self.status_code = 404

        def __str__(self):
            return "404 Not Found"

    mock_client.work_items._get.side_effect = MockHttpError()

    with pytest.raises(EntityResolutionError) as exc_info:
        resolver.resolve_ticket("ENG-999")

    assert "Ticket 'ENG-999' not found" in str(exc_info.value)


def test_resolve_ticket_context_sequence_and_uuid_resolution(resolver, mock_client):
    """Verify resolve_ticket_context resolves project context for both ENG-123 and raw ticket UUIDs."""
    p1 = MagicMock(id="proj-1", identifier="ENG", slug="eng")
    p1.name = "Engineering"
    p2 = MagicMock(id="proj-2", identifier="OPS", slug="ops")
    p2.name = "Operations"
    mock_client.projects.list.return_value = MagicMock(results=[p1, p2], next_cursor=None, next_page_results=False)

    ticket_uuid = "33333333-3333-3333-3333-333333333333"
    mock_client.work_items._get.return_value = {"id": ticket_uuid}

    # 1. Resolve by sequence ID (and matching project_slug)
    wid, proj_id, proj_ident = resolver.resolve_ticket_context("ENG-42")
    assert (wid, proj_id, proj_ident) == (ticket_uuid, "proj-1", "ENG")
    assert resolver.resolve_ticket_context("ENG-42", project_slug="ENG") == (ticket_uuid, "proj-1", "ENG")

    # Mismatched project_slug for sequence ID raises EntityResolutionError
    with pytest.raises(EntityResolutionError, match="belongs to project 'ENG', which does not match project_slug"):
        resolver.resolve_ticket_context("ENG-42", project_slug="OPS")

    # 2. Subsequent UUID lookup uses cached context from sequence resolution
    assert resolver.resolve_ticket_context(ticket_uuid) == (ticket_uuid, "proj-1", "ENG")

    # 3. UUID lookup with explicit project_slug
    explicit_uuid = "22222222-2222-2222-2222-222222222222"
    assert resolver.resolve_ticket_context(explicit_uuid, project_slug="OPS") == (
        explicit_uuid,
        "proj-2",
        "OPS",
    )

    # 4. UUID lookup resolved directly from work_items._get dict & model responses
    direct_uuid = "66666666-6666-6666-6666-666666666666"
    mock_client.work_items._get.return_value = {
        "id": direct_uuid,
        "project": {"id": "proj-1", "identifier": "eng"},
    }
    assert resolver.resolve_ticket_context(direct_uuid) == (direct_uuid, "proj-1", "ENG")

    # Expire ticket context cache and test object response extraction
    resolver._ts[f"ticket_ctx:{direct_uuid}"] = 0.0
    obj_resp = MagicMock(project=MagicMock(id="proj-2"), project_detail=MagicMock(identifier="ops"))
    obj_resp.project_id = None
    obj_resp.project_identifier = None
    mock_client.work_items._get.return_value = obj_resp
    assert resolver.resolve_ticket_context(direct_uuid) == (direct_uuid, "proj-2", "OPS")

    # 5. UUID lookup via project scan when uncached and _get fails
    uncached_uuid = "44444444-4444-4444-4444-444444444444"
    mock_client.work_items._get.side_effect = HttpError(status_code=404, message="Not Found")
    mock_client.work_items.retrieve.side_effect = [
        HttpError(status_code=404, message="Not Found"),
        MagicMock(id=uncached_uuid),
    ]

    assert resolver.resolve_ticket_context(uncached_uuid) == (uncached_uuid, "proj-2", "OPS")

    # 6. UUID lookup when not found in any project raises EntityResolutionError
    missing_uuid = "55555555-5555-5555-5555-555555555555"
    mock_client.work_items.retrieve.side_effect = [
        None,
        MagicMock(id="different-id"),
    ]
    with pytest.raises(EntityResolutionError, match=f"Ticket '{missing_uuid}' not found"):
        resolver.resolve_ticket_context(missing_uuid)


def test_resolve_ticket_context_error_branches(resolver, mock_client):
    """Verify resolve_ticket_context validation and error handling branches."""
    with pytest.raises(ValueError, match="Invalid ticket ID format"):
        resolver.resolve_ticket_context("INVALID")

    with pytest.raises(ValueError, match="Invalid ticket sequence"):
        resolver.resolve_ticket_context("ENG-0")

    assert resolver._extract_project_from_work_item(None) == (None, None)
    assert resolver._extract_project_from_work_item({"project_detail": {"id": "p-det", "identifier": "eng"}}) == (
        "p-det",
        "ENG",
    )

    t_uuid = "77777777-7777-7777-7777-777777777777"
    mock_client.work_items._get.return_value = {}
    mock_client.projects.list.side_effect = RuntimeError("Projects API down")
    with pytest.raises(RuntimeError, match="Failed to fetch projects while resolving ticket"):
        resolver.resolve_ticket_context(t_uuid)

    mock_client.projects.list.side_effect = None
    p_no_id = MagicMock(id=None)
    p_valid = MagicMock(id="proj-1", identifier="ENG")
    mock_client.projects.list.return_value = MagicMock(results=[p_no_id, p_valid], next_cursor=None)
    mock_client.work_items.retrieve.side_effect = HttpError(status_code=500, message="Server Error")
    with pytest.raises(RuntimeError, match="Failed to retrieve ticket"):
        resolver.resolve_ticket_context(t_uuid)


def test_resolve_page_tracks_expiry_per_cached_page(resolver, mock_client, monkeypatch):
    """Verify resolving page B does not extend the cache TTL of earlier-cached page A."""
    page_a = MagicMock(id="page-a-id")
    page_a.name = "Page A"
    page_b = MagicMock(id="page-b-id")
    page_b.name = "Page B"
    mock_client.pages.list_workspace_pages.return_value = MagicMock(results=[page_a, page_b])

    current_time = 1000.0
    monkeypatch.setattr(plane_mcp.resolver.time, "time", lambda: current_time)

    # Cache Page A at t=1000
    assert resolver.resolve_page("Page A") == ("page-a-id", None)
    assert mock_client.pages.list_workspace_pages.call_count == 1

    # Cache Page B at t=1200 (200s later)
    current_time = 1200.0
    assert resolver.resolve_page("Page B") == ("page-b-id", None)
    assert mock_client.pages.list_workspace_pages.call_count == 2

    # Advance to t=1350: Page A is 350s old (>300s TTL, expired), Page B is 150s old (<300s TTL, valid)
    current_time = 1350.0
    assert resolver.resolve_page("Page B") == ("page-b-id", None)
    assert mock_client.pages.list_workspace_pages.call_count == 2

    assert resolver.resolve_page("Page A") == ("page-a-id", None)
    assert mock_client.pages.list_workspace_pages.call_count == 3


def test_resolve_template_with_body_extracts_html(resolver, mock_client):
    """Verify resolve_template_with_body returns both the template ID and its HTML body."""
    tmpl = MagicMock(id="tmpl-1")
    tmpl.name = "ADR Template"
    tmpl.description_html = "<h1>Architecture Decision Record</h1><p>Context</p>"
    dict_tmpl = {"id": "tmpl-2", "name": "Dict Template", "body": "<p>Dict Body</p>"}
    mock_client.workspace_templates.pages.list.return_value = MagicMock(results=[tmpl, dict_tmpl])

    tmpl_id, body = resolver.resolve_template_with_body("ADR Template")
    assert tmpl_id == "tmpl-1"
    assert body == "<h1>Architecture Decision Record</h1><p>Context</p>"

    # Verify cached call returns both ID and body without extra API requests
    assert resolver.resolve_template_with_body("ADR Template") == (
        "tmpl-1",
        "<h1>Architecture Decision Record</h1><p>Context</p>",
    )
    assert mock_client.workspace_templates.pages.list.call_count == 1

    # Dict template extraction
    assert resolver.resolve_template_with_body("Dict Template") == ("tmpl-2", "<p>Dict Body</p>")
    assert _extract_template_body({}) is None

    # UUID fallback when template UUID is not in list or when list raises
    raw_uuid = "88888888-8888-8888-8888-888888888888"
    assert resolver.resolve_template_with_body(raw_uuid) == (raw_uuid, None)

    raw_uuid_2 = "99999999-9999-9999-9999-999999999999"
    mock_client.workspace_templates.pages.list.side_effect = RuntimeError("Offline")
    assert resolver.resolve_template_with_body(raw_uuid_2) == (raw_uuid_2, None)
