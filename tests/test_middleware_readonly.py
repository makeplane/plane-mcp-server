"""Tests for PlaneReadOnlyMiddleware and PLANE_READ_ONLY configuration.

Verifies:
- Read-only mode is disabled by default and only activates when explicitly set.
- All 82 declared read actions across the 30 resources are permitted.
- All 125 declared write actions across the 30 resources are blocked.
- All 169 legacy aliases are checked and routed to their canonical action classification.
- Single-action tools (e.g. get_pql_reference) are handled when action is omitted.
- Malformed, missing, or unknown actions/tools are rejected safely without crashes.
- Request arguments/payloads (which might carry PII or secrets) are never echoed.
- Negative tests using SpyClient prove rejected operations never reach the SDK.
- Permitted read operations pass through cleanly to the SDK.
"""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from plane.models.projects import Project
from plane.models.users import UserLite
from plane.models.work_items import PaginatedWorkItemResponse, WorkItem

from plane_mcp.middleware import PlaneReadOnlyMiddleware
from plane_mcp.server import _configured, get_header_mcp, get_oauth_mcp, get_stdio_mcp, is_read_only
from plane_mcp.tools.registry import RESOURCES, alias_table, unmapped_table
from tests.tools._spyclient import SpyClient

# --- 1. Configuration & Activation tests ---


def test_is_read_only_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PLANE_READ_ONLY", raising=False)
    assert is_read_only() is False


@pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "YES", "Yes", " true ", " 1 "])
def test_is_read_only_truthy_values(monkeypatch: pytest.MonkeyPatch, val: str):
    monkeypatch.setenv("PLANE_READ_ONLY", val)
    assert is_read_only() is True


@pytest.mark.parametrize("val", ["false", "False", "0", "no", "NO", "off", "OFF", "", "   ", " false "])
def test_is_read_only_falsy_values(monkeypatch: pytest.MonkeyPatch, val: str):
    monkeypatch.setenv("PLANE_READ_ONLY", val)
    assert is_read_only() is False


@pytest.mark.parametrize("val", ["ture", "random", "enabled", "2", "none", "read-only"])
def test_is_read_only_fails_fast_on_invalid_values(monkeypatch: pytest.MonkeyPatch, val: str):
    monkeypatch.setenv("PLANE_READ_ONLY", val)
    with pytest.raises(ValueError, match="Invalid PLANE_READ_ONLY value"):
        is_read_only()


def test_server_startup_fails_fast_on_invalid_read_only_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLANE_READ_ONLY", "ture")
    with pytest.raises(ValueError, match="Invalid PLANE_READ_ONLY value 'ture'"):
        get_stdio_mcp()


def test_server_configured_without_read_only_middleware_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PLANE_READ_ONLY", raising=False)

    mcp = FastMCP("test")
    _configured(mcp)
    types = [type(m) for m in mcp.middleware]
    assert PlaneReadOnlyMiddleware not in types


def test_server_configured_adds_middleware_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLANE_READ_ONLY", "true")

    mcp = FastMCP("test")
    _configured(mcp)
    types = [type(m) for m in mcp.middleware]
    assert PlaneReadOnlyMiddleware in types


def test_all_transports_receive_middleware_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-slug")
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_BASE_URL", "https://auth.plane.so")
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_CLIENT_ID", "test-id")
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET", "test-secret")

    for factory in (get_stdio_mcp, get_header_mcp, get_oauth_mcp):
        server = factory()
        types = [type(m) for m in server.middleware]
        assert PlaneReadOnlyMiddleware in types, f"{factory.__name__} did not register PlaneReadOnlyMiddleware"


# --- 2. Surface-wide invariant tests ---


@pytest.fixture(scope="module")
def middleware() -> PlaneReadOnlyMiddleware:
    return PlaneReadOnlyMiddleware()


def test_all_read_actions_permitted_surface_wide(middleware: PlaneReadOnlyMiddleware):
    """Every action declared with read=True must be permitted (rejection is None)."""
    verified = 0
    for mod in RESOURCES:
        for action in mod.ACTIONS:
            if action.read:
                refusal = middleware.rejection(mod.NAME, {"action": action.name})
                assert refusal is None, f"{mod.NAME}.{action.name} is marked read=True but was refused: {refusal}"
                verified += 1
    assert verified == 82, f"Expected 82 read actions across surface, verified {verified}"


def test_all_write_actions_blocked_surface_wide(middleware: PlaneReadOnlyMiddleware):
    """Every action declared with read=False must be refused."""
    blocked = 0
    for mod in RESOURCES:
        for action in mod.ACTIONS:
            if not action.read:
                refusal = middleware.rejection(mod.NAME, {"action": action.name})
                expected = f"Error: Plane MCP read-only mode blocks {mod.NAME}.{action.name}."
                assert refusal == expected, f"{mod.NAME}.{action.name} had unexpected refusal: {refusal}"
                blocked += 1
    assert blocked == 125, f"Expected 125 write actions across surface, blocked {blocked}"


def test_every_legacy_alias_correctly_classified(middleware: PlaneReadOnlyMiddleware):
    """Every one of the 169 retired aliases maps to its canonical action's read status."""
    aliases = alias_table()
    action_map = {mod.NAME: {a.name: a.read for a in mod.ACTIONS} for mod in RESOURCES}

    assert len(aliases) == 169
    for alias, (resource, action) in aliases.items():
        is_read = action_map[resource][action]
        refusal = middleware.rejection(alias, {})
        if is_read:
            assert refusal is None, f"Read alias {alias} -> {resource}.{action} was refused: {refusal}"
        else:
            expected = f"Error: Plane MCP read-only mode blocks {resource}.{action}."
            assert refusal == expected, f"Write alias {alias} -> {resource}.{action} expected {expected}, got {refusal}"


def test_single_action_tool_without_action_parameter(middleware: PlaneReadOnlyMiddleware):
    """Single-action tools take no action parameter; read-only must resolve their only action."""
    assert middleware.rejection("get_pql_reference", {}) is None
    assert middleware.rejection("get_pql_reference", None) is None


def test_unrecognised_tool_is_safely_blocked(middleware: PlaneReadOnlyMiddleware):
    refusal = middleware.rejection("nonexistent_unknown_tool", {})
    assert refusal == "Error: Plane MCP read-only mode blocks an unrecognised tool 'nonexistent_unknown_tool'."


def test_all_unmapped_legacy_tools_are_safely_blocked(middleware: PlaneReadOnlyMiddleware):
    """Unmapped legacy tools are not recognized actions, so read-only must block them fail-closed."""
    for tool_name in unmapped_table():
        refusal = middleware.rejection(tool_name, {})
        assert refusal == f"Error: Plane MCP read-only mode blocks an unrecognised tool '{tool_name}'."


def test_malformed_and_missing_arguments_safely_handled(middleware: PlaneReadOnlyMiddleware):
    """Ensure no exceptions occur when arguments are None, not a dict, or missing/malformed action."""
    assert middleware.rejection("workitem", None) == "Error: Plane MCP read-only mode blocks workitem.unknown."
    assert middleware.rejection("workitem", {}) == "Error: Plane MCP read-only mode blocks workitem.unknown."
    assert (
        middleware.rejection("workitem", {"action": None}) == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert (
        middleware.rejection("workitem", {"action": 123}) == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert (
        middleware.rejection("workitem", {"action": ["create", "delete"]})
        == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert (
        middleware.rejection("workitem", {"action": {"nested": "dict"}})
        == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert (
        middleware.rejection("workitem", {"action": set()})
        == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert middleware.rejection("workitem", {"action": 0}) == "Error: Plane MCP read-only mode blocks workitem.unknown."
    assert (
        middleware.rejection("workitem", {"action": False})
        == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert (
        middleware.rejection("workitem", {"action": ""}) == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )
    assert (
        middleware.rejection("workitem", {"action": "not_an_action"})
        == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )


def test_argument_payload_never_echoed_in_refusal(middleware: PlaneReadOnlyMiddleware):
    """Refusals must never echo arguments, which might contain private tokens or user data."""
    secret = "sk-super-secret-key-that-must-not-leak"
    refusal = middleware.rejection("workitem", {"action": "create", "secret_content": secret})
    assert refusal is not None
    assert secret not in refusal

    # Putting sensitive payloads into the action argument must also never be reflected
    refusal_secret_action = middleware.rejection("workitem", {"action": secret})
    assert refusal_secret_action == "Error: Plane MCP read-only mode blocks workitem.unknown."
    assert secret not in refusal_secret_action


# --- 3. End-to-end integration & SDK isolation tests ---


class _AllFeaturesOn:
    def model_dump(self):
        return {"initiatives": True, "customers": True, "releases": True, "epics": True}


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch):
    """Inject a recording SpyClient across all resource modules."""
    client = SpyClient()
    client.returns["workspaces.get_features"] = _AllFeaturesOn()
    # Populate returns with valid Pydantic models so FastMCP serializer succeeds
    client.returns["work_items.retrieve"] = WorkItem(id="w1", name="Item 1", project="p1")
    client.returns["work_items.list"] = PaginatedWorkItemResponse(
        results=[],
        total_count=0,
        next_cursor="",
        prev_cursor="",
        next_page_results=False,
        prev_page_results=False,
        count=0,
        total_pages=0,
        total_results=0,
    )
    client.returns["work_items.create"] = WorkItem(id="w1", name="Created Item", project="p1")
    client.returns["projects.retrieve"] = Project(id="p1", name="Project 1", identifier="PROJ")
    client.returns["users.get_me"] = UserLite(id="u1", email="me@example.com")

    for mod in RESOURCES:
        if hasattr(mod, "get_plane_client_context"):
            monkeypatch.setattr(mod, "get_plane_client_context", lambda: (client, "test-ws"))
    return client


@pytest.mark.anyio
async def test_read_only_mode_blocks_writes_before_sdk(monkeypatch: pytest.MonkeyPatch, spy: SpyClient):
    """Negative test: Prove that refused write operations NEVER reach the SDK."""
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    server = get_stdio_mcp()

    write_calls = [
        ("workitem", {"action": "create", "project_id": "p1", "name": "Test Item"}),
        ("workitem", {"action": "delete", "project_id": "p1", "workitem_id": "w1"}),
        ("workitem", {"action": "archive", "project_id": "p1", "workitem_id": "w1"}),
        ("project", {"action": "update", "project_id": "p1", "name": "Updated"}),
        ("cycle", {"action": "create", "project_id": "p1", "name": "Sprint 1", "owned_by": "u1"}),
        ("label", {"action": "delete", "project_id": "p1", "label_id": "l1"}),
        ("customer", {"action": "create", "name": "Acme Corp"}),
        ("milestone", {"action": "create", "project_id": "p1", "title": "v1.0"}),
        ("module", {"action": "archive", "project_id": "p1", "module_id": "m1"}),
        ("page", {"action": "create", "name": "New Page"}),
        (
            "workitem_attachment",
            {
                "action": "upload_from_url",
                "project_id": "p1",
                "workitem_id": "w1",
                "url": "https://example.com/file.png",
            },
        ),
        (
            "workitem_comment",
            {"action": "create", "project_id": "p1", "workitem_id": "w1", "comment_html": "<p>test</p>"},
        ),
        ("workitem_type", {"action": "resolve", "project_id": "p1", "name": "Bug"}),
        ("workspace", {"action": "update_features"}),
        ("state", {"action": "create", "project_id": "p1", "name": "Blocked State"}),
        (
            "workitem_property",
            {"action": "create", "project_id": "p1", "display_name": "CustomProp", "property_type": "text"},
        ),
        ("customer_property", {"action": "create", "display_name": "CustomCustProp", "property_type": "text"}),
        ("work_log", {"action": "create", "project_id": "p1", "workitem_id": "w1", "duration": 60}),
        ("workitem_relation", {"action": "create", "project_id": "p1", "workitem_id": "w1", "workitem_ids": ["w2"]}),
        ("create_issue", {"project_id": "p1", "name": "Legacy Issue"}),
        ("delete_issue", {"project_id": "p1", "work_item_id": "w1"}),
        # Malformed / unhashable action values must be cleanly rejected before the SDK
        ("workitem", {"action": ["create", "delete"], "project_id": "p1"}),
        ("workitem", {"action": {"nested": "dict"}, "project_id": "p1"}),
        ("workitem", {"action": 12345, "project_id": "p1"}),
    ]

    async with Client(server) as client:
        for tool_name, arguments in write_calls:
            # 1. Standard client call raises ToolError with genuine error semantics
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(tool_name, arguments)
            assert "Error: Plane MCP read-only mode blocks" in str(exc_info.value), (
                f"Expected read-only refusal in ToolError for {tool_name}"
            )

            # 2. Raw MCP protocol (raise_on_error=False) returns is_error=True
            result = await client.call_tool(tool_name, arguments, raise_on_error=False)
            assert result.is_error is True, f"Expected CallToolResult.is_error=True for {tool_name}"
            text = result.content[0].text if result.content else ""
            assert "Error: Plane MCP read-only mode blocks" in text, (
                f"Expected read-only refusal in CallToolResult for {tool_name}"
            )

    # Assert that zero calls reached the PlaneClient / SpyClient!
    assert len(spy.recorder.calls) == 0, f"Expected 0 SDK calls, but {len(spy.recorder.calls)} were recorded"


@pytest.mark.anyio
async def test_read_only_mode_permits_reads_to_sdk(monkeypatch: pytest.MonkeyPatch, spy: SpyClient):
    """Positive test: Prove that permitted read operations pass through and reach the SDK."""
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    server = get_stdio_mcp()

    read_calls = [
        ("get_pql_reference", {}),
        ("project", {"action": "retrieve", "project_id": "p1"}),
        ("workitem", {"action": "retrieve", "project_id": "p1", "workitem_id": "w1"}),
        ("member", {"action": "me"}),
        ("retrieve_work_item", {"project_id": "p1", "work_item_id": "w1"}),
    ]

    async with Client(server) as client:
        for tool_name, arguments in read_calls:
            result = await client.call_tool(tool_name, arguments)
            text = result.content[0].text if result.content else ""
            assert "Error: Plane MCP read-only mode blocks" not in text

    # Assert that read calls reached the PlaneClient / SpyClient
    assert len(spy.recorder.calls) > 0, "Expected SDK calls for permitted read operations"


@pytest.mark.anyio
async def test_disabled_by_default_permits_writes(monkeypatch: pytest.MonkeyPatch, spy: SpyClient):
    """When disabled, write operations pass through the middleware stack to the SDK."""
    monkeypatch.delenv("PLANE_READ_ONLY", raising=False)
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    server = get_stdio_mcp()

    async with Client(server) as client:
        result = await client.call_tool("workitem", {"action": "create", "project_id": "p1", "name": "Item"})
        text = result.content[0].text if result.content else ""
        assert "Error: Plane MCP read-only mode blocks" not in text

    # Write operation reached the SDK
    assert len(spy.recorder.calls) == 1
    assert spy.recorder.calls[0].method == "work_items.create"


@pytest.mark.anyio
async def test_middleware_order_preserves_validate_action_arguments(monkeypatch: pytest.MonkeyPatch):
    """When action is omitted on a multi-action tool, ValidateActionArguments reports the actions."""
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    server = get_stdio_mcp()

    async with Client(server) as client:
        result = await client.call_tool("workitem", {"project_id": "p1"})
        text = result.content[0].text if result.content else ""
        assert "Error: workitem requires an action. It takes:" in text


@pytest.mark.anyio
async def test_adversarial_alias_and_metadata_bypass_attempts(monkeypatch: pytest.MonkeyPatch, spy: SpyClient):
    """Verify that calling a read alias with injected write action or metadata mutation cannot bypass."""
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    server = get_stdio_mcp()

    async with Client(server) as client:
        # 1. Calling a read alias with an injected write action fails before dispatching mutation to SDK
        try:
            res = await client.call_tool(
                "retrieve_work_item",
                {"project_id": "p1", "work_item_id": "w1", "action": "delete"},
            )
        except ToolError as e:
            # FastMCP transformed tool rejects unexpected action argument
            assert "unexpected keyword argument" in str(e).lower()
        else:
            text = res.content[0].text if res.content else ""
            assert "Error" in text

        # 2. Uppercase or whitespace action names fail closed with genuine error semantics
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("workitem", {"action": "RETRIEVE", "project_id": "p1", "workitem_id": "w1"})
        assert "Error: Plane MCP read-only mode blocks workitem.unknown." in str(exc_info.value)

        raw_upper = await client.call_tool(
            "workitem",
            {"action": "RETRIEVE", "project_id": "p1", "workitem_id": "w1"},
            raise_on_error=False,
        )
        assert raw_upper.is_error is True
        assert "Error: Plane MCP read-only mode blocks workitem.unknown." in raw_upper.content[0].text

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("workitem", {"action": " retrieve", "project_id": "p1", "workitem_id": "w1"})
        assert "Error: Plane MCP read-only mode blocks workitem.unknown." in str(exc_info.value)

        raw_ws = await client.call_tool(
            "workitem",
            {"action": " retrieve", "project_id": "p1", "workitem_id": "w1"},
            raise_on_error=False,
        )
        assert raw_ws.is_error is True
        assert "Error: Plane MCP read-only mode blocks workitem.unknown." in raw_ws.content[0].text

    # Assert zero mutating calls reached SpyClient
    mutating_calls = [
        c
        for c in spy.recorder.calls
        if not any(
            c.method.endswith(f".{read_method}")
            for read_method in ("retrieve", "list", "get_me", "get_features", "summary", "count")
        )
    ]
    assert len(mutating_calls) == 0, f"Mutating calls reached SDK: {mutating_calls}"


@pytest.mark.anyio
async def test_raw_mcp_protocol_error_contract(monkeypatch: pytest.MonkeyPatch):
    """Verify that rejected writes return CallToolResult with is_error=True and sanitized text."""
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    server = get_stdio_mcp()

    async with Client(server) as client:
        # Check raw result structure
        raw = await client.call_tool(
            "workitem",
            {"action": "create", "project_id": "p1", "name": "Item"},
            raise_on_error=False,
        )
        assert raw.is_error is True
        assert len(raw.content) == 1
        assert raw.content[0].type == "text"
        assert raw.content[0].text == "Error: Plane MCP read-only mode blocks workitem.create."


@pytest.mark.anyio
async def test_action_and_tool_name_truncation_bounds_refusal_reflection(
    monkeypatch: pytest.MonkeyPatch, middleware: PlaneReadOnlyMiddleware, spy: SpyClient
):
    """Verify user-controlled action and tool names are bounded in refusal messages to prevent log/memory bloat."""
    monkeypatch.setenv("PLANE_READ_ONLY", "true")
    monkeypatch.setenv("PLANE_API_KEY", "test-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-ws")

    # 1. Direct middleware unit assertions
    long_action = "a" * 200
    refusal_action = middleware.rejection("workitem", {"action": long_action})
    assert refusal_action == "Error: Plane MCP read-only mode blocks workitem.unknown."
    assert long_action not in refusal_action

    long_tool = "tool_" + "x" * 200
    refusal_tool = middleware.rejection(long_tool, {})
    assert refusal_tool == f"Error: Plane MCP read-only mode blocks an unrecognised tool '{long_tool[:64]}'."
    assert long_tool not in refusal_tool

    # Unknown actions use fixed label "unknown" rather than reflecting user-provided values
    assert (
        middleware.rejection("workitem", {"action": "custom_unknown_action"})
        == "Error: Plane MCP read-only mode blocks workitem.unknown."
    )

    # 2. End-to-end client protocol assertions
    server = get_stdio_mcp()
    async with Client(server) as client:
        # Unknown action rejection has is_error=True and fixed "unknown" label
        raw = await client.call_tool(
            "workitem",
            {"action": "action_" + "z" * 150, "project_id": "p1"},
            raise_on_error=False,
        )
        assert raw.is_error is True
        assert raw.content[0].text == "Error: Plane MCP read-only mode blocks workitem.unknown."

        # Known write action is refused normally
        raw_write = await client.call_tool(
            "workitem",
            {"action": "create", "project_id": "p1", "name": "Item"},
            raise_on_error=False,
        )
        assert raw_write.is_error is True
        assert raw_write.content[0].text == "Error: Plane MCP read-only mode blocks workitem.create."

        # Known read action succeeds
        raw_read = await client.call_tool(
            "workitem",
            {"action": "retrieve", "project_id": "p1", "workitem_id": "w1"},
            raise_on_error=False,
        )
        assert raw_read.is_error is False

        # Legacy aliases behave consistently
        raw_alias_write = await client.call_tool(
            "create_work_item", {"project_id": "p1", "name": "Item"}, raise_on_error=False
        )
        assert raw_alias_write.is_error is True
        assert raw_alias_write.content[0].text == "Error: Plane MCP read-only mode blocks workitem.create."

        raw_alias_read = await client.call_tool(
            "retrieve_work_item", {"project_id": "p1", "work_item_id": "w1"}, raise_on_error=False
        )
        assert raw_alias_read.is_error is False

    # Assert zero mutating calls reached SpyClient
    mutating_calls = [
        c
        for c in spy.recorder.calls
        if not any(
            c.method.endswith(f".{read_method}")
            for read_method in ("retrieve", "list", "get_me", "get_features", "summary", "count")
        )
    ]
    assert len(mutating_calls) == 0, f"Mutating calls reached SDK: {mutating_calls}"
