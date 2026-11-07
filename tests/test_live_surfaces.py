"""Live HTTP surface tests for multi-surface Plane MCP routing, OAuth discovery, and tool handshakes."""

import json
import os
from unittest.mock import patch

os.environ["PLANE_OAUTH_PROVIDER_CLIENT_ID"] = "test-client-id"
os.environ["PLANE_OAUTH_PROVIDER_CLIENT_SECRET"] = "test-client-secret"
os.environ["PLANE_OAUTH_PROVIDER_BASE_URL"] = "http://localhost:8211"
os.environ["PLANE_BASE_URL"] = "https://api.plane.so"

import pytest
from starlette.testclient import TestClient

from plane_mcp.__main__ import create_app


@pytest.fixture
def client():
    """Yield a TestClient wrapping a freshly created multi-surface Starlette app."""
    # Fresh app per test to avoid StreamableHTTPSessionManager single-run reuse limitation
    app_instance = create_app()
    with TestClient(app_instance) as test_client:
        yield test_client


def test_well_known_oauth_endpoints(client, monkeypatch):
    """Verify root and per-mount RFC 8414/9728 OAuth discovery endpoints return surface-specific URLs."""
    res = client.get("/.well-known/oauth-authorization-server")
    assert res.status_code == 200

    # Per-mount protected resource & authorization server metadata for /mcp/docs/mcp and /docs/mcp
    res_pr_mcp_docs = client.get("/.well-known/oauth-protected-resource/mcp/docs/mcp")
    assert res_pr_mcp_docs.status_code == 200
    pr_data = res_pr_mcp_docs.json()
    assert pr_data["resource"].endswith("/mcp/docs/mcp")
    assert any(str(s).rstrip("/").endswith("/mcp/docs") for s in pr_data["authorization_servers"])

    res_as_mcp_docs = client.get("/.well-known/oauth-authorization-server/mcp/docs")
    assert res_as_mcp_docs.status_code == 200
    as_data = res_as_mcp_docs.json()
    assert as_data["authorization_endpoint"].endswith("/mcp/docs/authorize")

    # Verify PLANE_OAUTH_PROVIDER_ISSUER_URL still scopes issuer per mounted surface path
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_ISSUER_URL", "https://plane.example.com")
    with TestClient(create_app()) as custom_client:
        res_pr_issuer = custom_client.get("/.well-known/oauth-protected-resource/mcp/docs/mcp")
        assert res_pr_issuer.status_code == 200
        issuer_pr_data = res_pr_issuer.json()
        auth_servers = [str(s).rstrip("/") for s in issuer_pr_data["authorization_servers"]]
        assert "https://plane.example.com/mcp/docs" in auth_servers


def test_unauthenticated_api_key_header_rejection(client):
    """Verify unauthenticated requests to API-key surfaces are rejected with 401 or 403."""
    res_admin = client.post("/admin/api-key/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert res_admin.status_code in (401, 403)

    res_agent = client.post("/agent/api-key/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert res_agent.status_code in (401, 403)

    res_docs = client.post("/docs/api-key/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert res_docs.status_code in (401, 403)


def parse_sse_or_json(response):
    """Parse an SSE event-stream or JSON HTTP response into a decoded object."""
    text = response.text
    if text.startswith("event:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
    return response.json()


@patch("plane_mcp.auth.plane_header_auth_provider.PlaneHeaderAuthProvider._validate_api_key", return_value=True)
def test_authenticated_initialize_handshake(mock_validate, client):
    """Verify authenticated MCP initialize handshakes succeed across all surfaces and aliases."""
    headers = {
        "Authorization": "Bearer test-pat-token",
        "X-Workspace-Slug": "test-workspace",
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

    # Test /admin/api-key
    res_admin = client.post("/admin/api-key/mcp", json=init_payload, headers=headers)
    assert res_admin.status_code == 200
    data_admin = parse_sse_or_json(res_admin)
    assert "result" in data_admin
    assert data_admin["result"]["serverInfo"]["name"] == "Plane MCP Server (header-http)"

    # Test /mcp/admin/api-key
    res_mcp_admin = client.post("/mcp/admin/api-key/mcp", json=init_payload, headers=headers)
    assert res_mcp_admin.status_code == 200

    # Test /agent/api-key
    res_agent = client.post("/agent/api-key/mcp", json=init_payload, headers=headers)
    assert res_agent.status_code == 200
    data_agent = parse_sse_or_json(res_agent)
    assert "result" in data_agent
    assert data_agent["result"]["serverInfo"]["name"] == "Plane Agent MCP Server (header-http)"

    # Test /mcp/agent/api-key
    res_mcp_agent = client.post("/mcp/agent/api-key/mcp", json=init_payload, headers=headers)
    assert res_mcp_agent.status_code == 200

    # Test /docs/api-key
    res_docs = client.post("/docs/api-key/mcp", json=init_payload, headers=headers)
    assert res_docs.status_code == 200
    data_docs = parse_sse_or_json(res_docs)
    assert "result" in data_docs
    assert data_docs["result"]["serverInfo"]["name"] == "Plane Docs MCP Server (header-http)"

    # Test /mcp/docs/api-key
    res_mcp_docs = client.post("/mcp/docs/api-key/mcp", json=init_payload, headers=headers)
    assert res_mcp_docs.status_code == 200


@patch("plane_mcp.auth.plane_header_auth_provider.PlaneHeaderAuthProvider._validate_api_key", return_value=True)
def test_surface_tools_list(mock_validate, client):
    """Verify tools/list returns the isolated tool catalogue expected for each surface."""
    headers = {
        "Authorization": "Bearer test-pat-token",
        "X-Workspace-Slug": "test-workspace",
        "Accept": "application/json, text/event-stream",
    }
    list_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }

    # Docs surface tool check
    res_docs = client.post("/mcp/docs/api-key/mcp", json=list_payload, headers=headers)
    assert res_docs.status_code == 200
    docs_tools = [t["name"] for t in parse_sse_or_json(res_docs)["result"]["tools"]]
    assert set(docs_tools) == {"search_docs", "read_doc", "create_doc", "update_doc", "link_doc", "organize_docs"}

    # Agent surface tool check
    res_agent = client.post("/mcp/agent/api-key/mcp", json=list_payload, headers=headers)
    assert res_agent.status_code == 200
    agent_tools = [t["name"] for t in parse_sse_or_json(res_agent)["result"]["tools"]]
    assert "read_ticket" in agent_tools
    assert "create_ticket" in agent_tools
    assert "update_ticket" in agent_tools

    # Admin surface tool check
    res_admin = client.post("/mcp/admin/api-key/mcp", json=list_payload, headers=headers)
    assert res_admin.status_code == 200
    admin_tools = [t["name"] for t in parse_sse_or_json(res_admin)["result"]["tools"]]
    assert len(admin_tools) >= 30
