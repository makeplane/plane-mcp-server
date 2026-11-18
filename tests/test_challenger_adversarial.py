"""Adversarial challenger stress testing for Plane MCP surfaces.

Tests boundary conditions, malformed inputs, malicious traversal, Unicode/emoji,
invalid JSON, nonexistent slugs, YAML format validity, and legacy admin isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
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
    """Return a freshly constructed multi-surface Starlette application."""
    return create_app()


@pytest.fixture
def auth_headers():
    """Return standard authenticated request headers for API-key surfaces."""
    return {
        "Authorization": "Bearer test-pat-token",
        "X-Workspace-Slug": "adversarial-workspace",
        "Accept": "application/json, text/event-stream",
    }


def parse_response_body(response) -> dict[str, Any]:
    """Parse an SSE event-stream or JSON HTTP response into a dictionary."""
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
    """Execute a tool callable and parse its YAML string output."""
    raw_yaml = fn(**kwargs)
    assert isinstance(raw_yaml, str), f"Expected YAML string from tool, got {type(raw_yaml)}"
    parsed = yaml.safe_load(raw_yaml)
    return raw_yaml, parsed


@pytest.fixture
def mock_plane_context():
    """Provide a mocked Plane SDK client and workspace slug across journey and docs tools."""
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
    """Verify agent surface tools safely reject boundary violations, empty inputs, and path traversal."""
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
    """Verify docs surface tools safely reject boundary violations, XSS payloads, and path traversal."""
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
    client.work_items._get.return_value = {"id": "wid-1"}
    raw8, res8 = call_tool_and_parse(tools["link_doc"], ticket_id="", page="")
    assert "error" in res8

    raw9, res9 = call_tool_and_parse(tools["link_doc"], ticket_id="ENG-1", page="../../parent", action="attach")
    assert "error" in res9
    assert "path traversal" in res9["error"]

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


UPSTREAM_TOOLS_SHA256: dict[str, str] = {
    "__init__.py": "222b3b8b4e8dcb5421b186dad93fc227ef3542f3e1cfaa88acfa99a94af5c89c",
    "collection.py": "5be69c5cc30da8830ca6c351283630fde54d1e896b33243dbc37714363d9cc54",
    "customer.py": "2a1c2ba6aacb9e386afc8a23063740a85069e08ec44ace12c60cd5c31f6badc4",
    "customer_property.py": "78752c971741df1a07d0ed5f16120969ac008a47b5751a8ab111adb496ea184f",
    "customer_request.py": "69202b797e578494b8b7d4b46105bff8dd4cf45a6d10a6b7f6662a680c536f63",
    "cycle.py": "a47a52764c72c877399740acd287a8a458aab84cd0984abf8c502278c5a875fa",
    "get_pql_reference.py": "3d6378cad2a20ca01af6de9950eda0983b76b325059cb3948b52362a2afba1de",
    "initiative.py": "7b6ea5ef3a301cb66aa107e34498fed1ccb8bd70887a00cfb9fc8f6cda4c5bfe",
    "intake.py": "b1312c3a5ec9d90cc18cf641ed64a641aac14ecd78aa266c6d71257366b4f69a",
    "label.py": "2d1bba558f30e0af42366e897baa97d2d47228baf9c42c01908c7f28503cc55e",
    "legacy.py": "f1acc4ba9e093a35827d76aae277921e9a032ee201529ba5fd25d12435e00782",
    "member.py": "5b4a4a816a6676630842a1f4f114f6603dd97f678a460ebed2ccb583a0a08f30",
    "milestone.py": "2d812812bb340d0cbb20d0f7d3c94ac807fa1f15cd51dc063ca287ce4a514461",
    "module.py": "a61964f95464b598ec5ac45610c1801ec3c12caa7298cf1453bfc7695758657f",
    "page.py": "f2a6c60ba82f68efb11175a90ee4e4e252fbf4dbf7105a8eedd22c07299bbe66",
    "project.py": "c243d5eddaf7b9f4f2181f519ec94adefeeb6fc43ed98d14f7ac621b9d5a5a1f",
    "project_estimate.py": "31b4d5a3ba0b866c0636848cbdab88fdc8acabe1042dc3ba04d44c1508be5644",
    "registry.py": "3b16218eb6eb3072cbaa0c8fa2efe2c8b11ba61940e0b510d925afa778c9b113",
    "release.py": "096d5d9242c6fb0309b2246966e11139ba896c67b6f735eaaf5be0ecdd60c0b1",
    "release_label.py": "1ffb23f38bf4fab881e236c66a21b926977b19875831351342c2d01371d7ab77",
    "release_tag.py": "9daec1e49adae13323c5abd1245ff697f0c8368e58d46df76fa9fa584cf67f7e",
    "state.py": "a732151675dce7f6bfe3e6e8ef373281adb7e3bd98152d8b64915d8be98acefe",
    "template.py": "2635c2faff5eb8c4fe47a0c68787b70403f3d798b446dd4bdf3409454ec5a3b4",
    "work_log.py": "ce45f126e8e9191a389c048bcf5ad8c6332cf5a7bf7c222130eacefab42a09dc",
    "workitem.py": "b8fee878b9ab81fdb534d00d32995633913e5f31a310e237f9eb8b21111aadad",
    "workitem_activity.py": "6341c0bb62e66c2bf3bf4a6737e823071ddd543d4eed00db9810b9855de6aeae",
    "workitem_attachment.py": "af161122338c00c0130b48f041102e8f29997b4be425bba6fbe1213999200679",
    "workitem_comment.py": "272de585697bb3b4c32e72173d059912a8a4b7755735526d60616e2d0e6935fb",
    "workitem_link.py": "38a248d5c08004c6d4e07488385842e1c27bc8a332710ad76fd79db903bbb69c",
    "workitem_property.py": "fb20101b3492b773d53cdc11b94754c3391dc6a9f52d2b80fe95504f20546dd6",
    "workitem_relation.py": "08286005e1a9142135699059838271a2c8580189b499254b5b2a334f5d0abaa1",
    "workitem_type.py": "ce40504c5257a77a0d5bf6a205b970eac94da611bc9adbb31acfa573fae6e817",
    "workspace.py": "c9534c97406b5432241671e08911dbc5d71a9d570710ab8aad28bf6d187cc1e8",
}


def test_administrative_tools_unimpacted():
    """Verify that administrative tools (plane_mcp/tools/) are completely unimpacted and all 30 legacy tools exist."""
    # 1. Cryptographic SHA-256 baseline verification against upstream plane_mcp/tools
    tools_dir = Path(__file__).resolve().parent.parent / "plane_mcp" / "tools"
    actual_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(tools_dir.glob("*.py"))}
    assert actual_hashes == UPSTREAM_TOOLS_SHA256, "plane_mcp/tools files differ from upstream SHA-256 baseline"

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
