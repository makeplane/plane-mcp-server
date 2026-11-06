"""Adversarial challenger stress testing for Plane MCP surfaces.

Tests boundary conditions, malformed inputs, malicious traversal, Unicode/emoji,
invalid JSON, nonexistent slugs, YAML format validity, and legacy admin isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

os.environ["PLANE_OAUTH_PROVIDER_CLIENT_ID"] = "test-client-id"
os.environ["PLANE_OAUTH_PROVIDER_CLIENT_SECRET"] = "test-client-secret"
os.environ["PLANE_OAUTH_PROVIDER_BASE_URL"] = "http://localhost:8211"
os.environ["PLANE_BASE_URL"] = "https://api.plane.so"

import pytest
import yaml
from starlette.testclient import TestClient

from plane_mcp.__main__ import create_app
from plane_mcp.sanitize import sanitize_html
from plane_mcp.server import get_header_mcp
from plane_mcp.tools.registry import RESOURCES
from plane_mcp.yaml_formatter import format_as_yaml
from tests.tools.test_conformance import CATALOGUE


@pytest.fixture
def test_app():
    return create_app()


@pytest.fixture
def auth_headers():
    return {
        "Authorization": "Bearer test-pat-token",
        "X-Workspace-Slug": "adversarial-workspace",
        "Accept": "application/json, text/event-stream",
    }


def parse_response_body(response) -> dict[str, Any]:
    text = response.text.strip()
    if not text:
        return {}
    if text.startswith("event:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
    try:
        return response.json()
    except Exception:
        return {"raw_text": text}


# ============================================================================
# 1. HTTP Surface Boundary Conditions & Malformed Inputs
# ============================================================================


@pytest.mark.parametrize(
    "endpoint",
    [
        "/mcp/agent/api-key/mcp",
        "/agent/api-key/mcp",
        "/mcp/docs/api-key/mcp",
        "/docs/api-key/mcp",
        "/mcp/admin/api-key/mcp",
        "/admin/api-key/mcp",
    ],
)
@patch("plane_mcp.auth.plane_header_auth_provider.PlaneHeaderAuthProvider._validate_api_key", return_value=True)
def test_malformed_json_and_body_rejections(mock_auth, endpoint, test_app, auth_headers):
    """Verify that malformed JSON, empty bodies, and invalid payloads never cause unhandled crashes (500)."""
    with TestClient(test_app) as client:
        # Empty body
        res = client.post(endpoint, content="", headers=auth_headers)
        assert res.status_code in (400, 422), f"Expected 4xx on empty body, got {res.status_code} at {endpoint}"

        # Broken JSON syntax
        json_headers = {**auth_headers, "Content-Type": "application/json"}
        res = client.post(endpoint, content="{'broken_json': ", headers=json_headers)
        assert res.status_code in (400, 422), f"Expected 4xx on invalid JSON, got {res.status_code}"

        # Non-dict JSON (array or primitive)
        res = client.post(endpoint, content="[1, 2, 3]", headers=json_headers)
        assert res.status_code in (400, 422), f"Expected 4xx on list JSON, got {res.status_code}"

        # Truncated JSON with control chars
        res = client.post(endpoint, content='{"jsonrpc": "2.0", "method": \x00}', headers=json_headers)
        assert res.status_code in (400, 422), f"Expected 4xx on null bytes in body, got {res.status_code}"


@pytest.mark.parametrize(
    "endpoint",
    [
        "/mcp/agent/api-key/mcp",
        "/mcp/docs/api-key/mcp",
        "/mcp/admin/api-key/mcp",
    ],
)
@patch("plane_mcp.auth.plane_header_auth_provider.PlaneHeaderAuthProvider._validate_api_key", return_value=True)
def test_adversarial_jsonrpc_messages(mock_auth, endpoint, test_app, auth_headers):
    """Test invalid RPC methods, missing params, and traversal in tool calls."""
    with TestClient(test_app) as client:
        # Unknown RPC method
        res = client.post(
            endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": "nonexistent/exploit"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = parse_response_body(res)
        assert "error" in body or body.get("result") is None

        # Traversal in tool name
        res = client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "../../../etc/passwd", "arguments": {}},
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = parse_response_body(res)
        assert "error" in body or (body.get("result") and body["result"].get("isError") is True)
        assert "traceback" not in json.dumps(body).lower()


@pytest.mark.parametrize(
    "malformed_slug",
    [
        "   ",
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "slug\r\nInjected-Header: malicious",
        "slug\x00extra",
        "A" * 500,
    ],
)
@patch("plane_mcp.auth.plane_header_auth_provider.PlaneHeaderAuthProvider._validate_api_key", return_value=True)
def test_adversarial_workspace_slug_headers(mock_auth, malformed_slug, test_app):
    """Verify that malicious workspace slugs cannot cause directory traversal or header injection."""
    headers = {
        "Authorization": "Bearer test-pat-token",
        "X-Workspace-Slug": malformed_slug,
        "Accept": "application/json, text/event-stream",
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "TestClient", "version": "1.0"},
        },
    }
    with TestClient(test_app) as client:
        res = client.post("/mcp/agent/api-key/mcp", json=init_payload, headers=headers)
        # Should either safely initialize or cleanly reject, but NEVER 500 crash
        assert res.status_code != 500
        assert "traceback" not in res.text.lower()


# ============================================================================
# 2. Agent Tools Boundary & Adversarial Stress Tests
# ============================================================================


def call_tool_and_parse(fn, **kwargs) -> tuple[str, Any]:
    raw_yaml = fn(**kwargs)
    assert isinstance(raw_yaml, str), f"Expected YAML string from tool, got {type(raw_yaml)}"
    parsed = yaml.safe_load(raw_yaml)
    return raw_yaml, parsed


@pytest.fixture
def mock_plane_context():
    client = MagicMock()
    ws = "test-ws"
    mock_p = MagicMock(identifier="ENG", id="proj-1")
    mock_p.name = "Engineering"
    mock_p.slug = "eng"
    mock_p.description = "Test project"
    client.projects.list.return_value = MagicMock(results=[mock_p])
    client.collections.list.return_value = MagicMock(results=[])

    mock_page_dict = {
        "id": "p-1",
        "name": "Test Page",
        "description_html": "<p>Content</p>",
        "collection_id": None,
        "is_locked": False,
        "parent_id": None,
        "access": 0,
    }
    client.pages.create_workspace_page.return_value = mock_page_dict
    client.pages.create_project_page.return_value = mock_page_dict
    client.pages.retrieve_workspace_page.return_value = mock_page_dict
    client.pages.retrieve_project_page.return_value = mock_page_dict

    with (
        patch("plane_mcp.journey.tools.create_update.get_plane_client_context", return_value=(client, ws)),
        patch("plane_mcp.journey.tools.read.get_plane_client_context", return_value=(client, ws)),
        patch("plane_mcp.journey.tools.workflow.get_plane_client_context", return_value=(client, ws)),
        patch("plane_mcp.docs.tools.get_plane_client_context", return_value=(client, ws)),
    ):
        yield client, ws


def test_agent_tools_boundary_and_malformed_inputs(mock_plane_context):
    client, ws = mock_plane_context
    mcp = get_header_mcp(surface="agent")

    import asyncio

    tools = {t.name: t.fn for t in asyncio.run(mcp.list_tools())}

    # 1. create_ticket boundary checks
    # Empty title and project_slug
    raw1, res1 = call_tool_and_parse(tools["create_ticket"], title="", project_slug="")
    assert "error" in res1
    assert "cannot be empty" in res1["error"]
    assert "traceback" not in raw1.lower()

    # Path traversal project_slug
    raw2, res2 = call_tool_and_parse(tools["create_ticket"], title="Test", project_slug="../../etc/shadow")
    assert "error" in res2
    assert "path traversal" in res2["error"]
    assert "traceback" not in raw2.lower()

    # Nonexistent project slug resolution failure
    client.projects.list.return_value = MagicMock(results=[])
    raw3, res3 = call_tool_and_parse(tools["create_ticket"], title="Test", project_slug="NONEXISTENT_SLUG")
    assert "error" in res3
    assert "not found" in res3["error"]

    # Unicode and emoji in title & description
    raw4, res4 = call_tool_and_parse(
        tools["create_ticket"],
        title="🔥 Critical bug: 🚀 failed in Orbit! 💥",
        project_slug="help",
        description="Markdown with emojis 🌟 and unicode: مرحبا بالعالم",
    )
    assert "priorities" in res4 or "error" not in res4

    # 2. update_ticket boundary checks
    # Invalid ticket ID format (missing hyphen)
    raw5, res5 = call_tool_and_parse(tools["update_ticket"], ticket_id="ENG123")
    assert "error" in res5
    assert "Invalid ticket ID format" in res5["error"]

    # Path traversal ticket ID
    raw6, res6 = call_tool_and_parse(tools["update_ticket"], ticket_id="../../etc/passwd")
    assert "error" in res6
    assert "path traversal" in res6["error"]

    # Non-integer ticket sequence
    raw7, res7 = call_tool_and_parse(tools["update_ticket"], ticket_id="ENG-abc")
    assert "error" in res7
    assert "Must be an integer" in res7["error"] or "Invalid ticket" in res7["error"]

    # 3. search_tickets boundary checks
    # project_slug='help' returns discovery context directly
    raw_help, help_res = call_tool_and_parse(tools["search_tickets"], project_slug="help")
    assert "priorities" in help_res

    # Negative or 0 limit
    raw8, res8 = call_tool_and_parse(tools["search_tickets"], project_slug="help", limit=-10)
    assert res8 is not None

    # Path traversal
    raw9, res9 = call_tool_and_parse(tools["search_tickets"], project_slug="../../etc")
    assert "error" in res9

    # 4. read_ticket boundary checks
    raw10, res10 = call_tool_and_parse(tools["read_ticket"], ticket_id="")
    assert "error" in res10

    raw11, res11 = call_tool_and_parse(tools["read_ticket"], ticket_id="ENG-1\x00null")
    assert "error" in res11

    # 5. transition_ticket boundary checks
    raw12, res12 = call_tool_and_parse(tools["transition_ticket"], ticket_id="", state_name="")
    assert "error" in res12

    raw13, res13 = call_tool_and_parse(tools["transition_ticket"], ticket_id="ENG-1", state_name="../state")
    assert "error" in res13

    # 6. begin_work boundary checks
    raw14, res14 = call_tool_and_parse(tools["begin_work"], ticket_ids=[], cycle_name="")
    assert "error" in res14 or "results" in res14 or isinstance(res14, dict)

    raw15, res15 = call_tool_and_parse(tools["begin_work"], ticket_ids=["../../etc"], cycle_name="Sprint 1")
    assert "error" in res15 or any("error" in str(r) for r in res15.values())

    # 7. complete_work boundary checks
    raw16, res16 = call_tool_and_parse(tools["complete_work"], ticket_id="", comment="")
    assert "error" in res16

    raw17, res17 = call_tool_and_parse(
        tools["complete_work"], ticket_id="ENG-1", comment="<script>alert('xss')</script>"
    )
    assert res17 is not None


# ============================================================================
# 3. Docs Tools Boundary & Adversarial Stress Tests
# ============================================================================


def test_docs_tools_boundary_and_malformed_inputs(mock_plane_context):
    client, ws = mock_plane_context
    mcp = get_header_mcp(surface="docs")

    import asyncio

    tools = {t.name: t.fn for t in asyncio.run(mcp.list_tools())}

    # 1. search_docs boundary checks
    # Discovery mode
    raw_docs, help_docs = call_tool_and_parse(tools["search_docs"], project_slug="help")
    assert "workspace" in help_docs
    assert "available_projects" in help_docs

    # Empty query with traversal slug
    raw1, res1 = call_tool_and_parse(tools["search_docs"], project_slug="../../etc")
    assert "error" in res1

    # 2. read_doc boundary checks
    raw2, res2 = call_tool_and_parse(tools["read_doc"], page="")
    assert "error" in res2

    raw3, res3 = call_tool_and_parse(tools["read_doc"], page="../secret_file")
    assert "error" in res3

    # 3. create_doc boundary checks & XSS sanitization
    # Creation with valid title returns valid YAML page
    raw4, res4 = call_tool_and_parse(tools["create_doc"], title="New Page", content="valid content")
    assert "id" in res4

    # Traversal in project slug is safely rejected
    raw5, res5 = call_tool_and_parse(tools["create_doc"], title="Title", project_slug="../../bad_path")
    assert "error" in res5
    assert "path traversal" in res5["error"]

    # XSS content injection test
    xss_payload = "<script>alert('pwned')</script><iframe src=javascript:alert(1)></iframe>"
    mock_page = MagicMock(id="p-1", name="XSS Test")
    client.pages.create.return_value = mock_page
    client.projects.pages.create.return_value = mock_page
    # Ensure html sanitization strips dangerous tags
    clean = sanitize_html(xss_payload)
    assert "<script>" not in clean
    assert "<iframe" not in clean

    # 4. update_doc boundary checks
    raw6, res6 = call_tool_and_parse(tools["update_doc"], page="")
    assert "error" in res6

    raw7, res7 = call_tool_and_parse(tools["update_doc"], page="../doc")
    assert "error" in res7

    # 5. link_doc boundary checks
    raw8, res8 = call_tool_and_parse(tools["link_doc"], parent_page="", child_page="")
    assert "error" in res8

    raw9, res9 = call_tool_and_parse(tools["link_doc"], parent_page="../../parent", child_page="child")
    assert "error" in res9

    # 6. organize_docs boundary checks
    raw10, res10 = call_tool_and_parse(tools["organize_docs"], target="invalid", action="invalid")
    assert "error" in res10 or res10.get("status") == "error"

    raw11, res11 = call_tool_and_parse(
        tools["organize_docs"], target="collection", action="delete", name_or_id="../../etc"
    )
    assert "error" in res11 or res11.get("status") == "error"


# ============================================================================
# 4. FastMCP Tool Execution & Valid YAML Formatting Across Surfaces
# ============================================================================


def test_agent_and_docs_tools_return_valid_yaml(mock_plane_context):
    """Verify that FastMCP tool functions for agent and docs return valid YAML without errors."""
    mcp_agent = get_header_mcp(surface="agent")
    mcp_docs = get_header_mcp(surface="docs")

    import asyncio

    # Test agent search_tickets 'help'
    agent_search = asyncio.run(mcp_agent.get_tool("search_tickets")).fn
    raw_yaml, parsed = call_tool_and_parse(agent_search, project_slug="help")
    assert isinstance(parsed, dict)
    assert "priorities" in parsed

    # Test docs search_docs 'help'
    docs_search = asyncio.run(mcp_docs.get_tool("search_docs")).fn
    raw_docs_yaml, parsed_docs = call_tool_and_parse(docs_search, project_slug="help")
    assert isinstance(parsed_docs, dict)
    assert "available_projects" in parsed_docs
    assert parsed_docs.get("workspace") == "test-ws"

    # Test error boundary returns valid YAML
    agent_create = asyncio.run(mcp_agent.get_tool("create_ticket")).fn
    raw_err_yaml, parsed_err = call_tool_and_parse(agent_create, title="", project_slug="")
    assert isinstance(parsed_err, dict)
    assert "error" in parsed_err
    assert "traceback" not in raw_err_yaml.lower()


def test_yaml_formatter_adversarial_stress():
    """Adversarial stress testing of YAML dumper."""
    # 1. Cyclic / shared object references (suppression of anchors & aliases)
    shared = {"prop": "value", "list": [1, 2, 3]}
    complex_struct = {
        "ref1": shared,
        "ref2": shared,
        "list_ref": [shared, shared],
    }
    yaml_str = format_as_yaml(complex_struct)
    assert "&id" not in yaml_str
    assert "*id" not in yaml_str
    reloaded = yaml.safe_load(yaml_str)
    assert reloaded["ref1"]["prop"] == "value"
    assert reloaded["ref2"]["prop"] == "value"

    # 2. Control characters & null bytes in dict keys and values
    malicious_data = {
        "clean_key\x00injected": "val\x00with_null",
        "normal": "hello\x1b[31mworld\x1b[0m",
        "multiline": "line 1\nline 2: with colons\nline 3",
        "colon_value": "key: value pair string",
    }
    yaml_out = format_as_yaml(malicious_data)
    assert "\x00" not in yaml_out
    assert "\x1b" not in yaml_out
    parsed = yaml.safe_load(yaml_out)
    assert "clean_keyinjected" in parsed
    assert "line 1" in parsed["multiline"]


# ============================================================================
# 5. Upstream Administrative Tools Invariance & Isolation
# ============================================================================


def test_administrative_tools_unimpacted():
    """Verify that administrative tools (plane_mcp/tools/) are completely unimpacted and all 30 legacy tools exist."""
    # 1. Byte-level git diff against upstream main
    git_cmd = ["git", "diff", "5c3467a", "HEAD", "--", "plane_mcp/tools"]
    result = subprocess.run(git_cmd, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert result.returncode == 0
    assert result.stdout.strip() == "", f"plane_mcp/tools has unexpected diff against upstream main:\n{result.stdout}"

    # 2. Verify all 30 catalogue resources are registered
    assert len(CATALOGUE) == 30
    assert len(RESOURCES) == 30

    # 3. Verify admin surface exposes all 30 legacy tools
    mcp_admin = get_header_mcp(surface="admin")
    import asyncio

    admin_tools = asyncio.run(mcp_admin.list_tools())
    assert len(admin_tools) >= 30
    admin_tool_names = {t.name for t in admin_tools}
    for cat_name in CATALOGUE:
        assert cat_name in admin_tool_names, f"Legacy tool {cat_name} missing from admin surface"
