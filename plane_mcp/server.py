"""FastMCP server factories for the supported transports across admin, agent, and docs surfaces."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Literal

from fastmcp import FastMCP
from mcp.types import Icon

from plane_mcp.auth import PlaneHeaderAuthProvider, PlaneOAuthProvider
from plane_mcp.instructions import SERVER_INSTRUCTIONS
from plane_mcp.middleware import CoerceArguments, PlaneLoggingMiddleware, ValidateActionArguments
from plane_mcp.storage import build_token_store
from plane_mcp.toolkit import StripOutputSchemas
from plane_mcp.tools import register_tools
from plane_mcp.yaml_formatter import YamlOutputMiddleware

SurfaceType = Literal["admin", "agent", "docs"]

DEFAULT_ALLOWED_REDIRECT_URIS = [
    "http://localhost:*",
    "http://localhost:*/*",
    "http://127.0.0.1:*",
    "http://127.0.0.1:*/*",
    "cursor://anysphere.cursor-mcp/oauth/*",
    "https://www.cursor.com/*",
    "https://vscode.dev/redirect",
    "https://insiders.vscode.dev/redirect",
    "https://antigravity.google/oauth-callback",
    "https://claude.ai/*",
    "https://chatgpt.com/connector/oauth/*",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://oauth-redirect.googleusercontent.com/r/*",
    "https://grok.com/connectors-oauth-exchange-code/",
]


def get_allowed_client_redirect_uris() -> list[str]:
    allowed = list(DEFAULT_ALLOWED_REDIRECT_URIS)
    extra = os.getenv("PLANE_OAUTH_ALLOWED_REDIRECT_URIS", "")
    for uri in extra.split(","):
        uri = uri.strip()
        if uri and uri not in allowed:
            allowed.append(uri)
    return allowed


LOG_PAYLOADS = os.getenv("LOG_PAYLOADS", "true").lower() == "true"


def _get_surface_registrar(surface: SurfaceType) -> tuple[str, Callable[[FastMCP], None]]:
    if surface == "agent":
        from plane_mcp.journey.tools import register_tools as register_agent

        return "Plane Agent MCP Server", register_agent
    if surface == "docs":
        from plane_mcp.docs.tools import register_docs_tools

        return "Plane Docs MCP Server", register_docs_tools
    return "Plane MCP Server", register_tools


def _configure(mcp: FastMCP, surface: SurfaceType = "admin") -> FastMCP:
    mcp.add_middleware(PlaneLoggingMiddleware(include_payloads=LOG_PAYLOADS))
    mcp.add_middleware(CoerceArguments())
    mcp.add_middleware(ValidateActionArguments())
    mcp.add_middleware(YamlOutputMiddleware())
    mcp.add_transform(StripOutputSchemas())

    _, registrar = _get_surface_registrar(surface)
    registrar(mcp)
    return mcp


def get_oauth_mcp(base_path: str = "/", surface: SurfaceType = "admin") -> FastMCP:
    name, _ = _get_surface_registrar(surface)
    oauth_mcp = FastMCP(
        name,
        instructions=SERVER_INSTRUCTIONS if surface == "admin" else None,
        icons=[Icon(src="https://plane.so/favicon.ico", alt=name)],
        website_url="https://plane.so",
        auth=PlaneOAuthProvider(
            client_id=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID", ""),
            client_secret=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET", ""),
            base_url=f"{os.getenv('PLANE_OAUTH_PROVIDER_BASE_URL', 'http://localhost:8211')}{base_path}",
            plane_base_url=os.getenv("PLANE_BASE_URL", ""),
            plane_internal_base_url=os.getenv("PLANE_INTERNAL_BASE_URL", ""),
            enable_cimd=os.getenv("PLANE_OAUTH_PROVIDER_ENABLE_CIMD", "false").lower() == "true",
            client_storage=build_token_store(),
            required_scopes=["read", "write"],
            allowed_client_redirect_uris=get_allowed_client_redirect_uris(),
        ),
    )
    return _configure(oauth_mcp, surface=surface)


def get_header_mcp(surface: SurfaceType = "admin") -> FastMCP:
    name, _ = _get_surface_registrar(surface)
    header_mcp = FastMCP(
        f"{name} (header-http)",
        instructions=SERVER_INSTRUCTIONS if surface == "admin" else None,
        auth=PlaneHeaderAuthProvider(
            required_scopes=["read", "write"],
        ),
    )
    return _configure(header_mcp, surface=surface)


def get_stdio_mcp(surface: SurfaceType = "admin") -> FastMCP:
    name, _ = _get_surface_registrar(surface)
    stdio_mcp = FastMCP(
        f"{name} (stdio)",
        instructions=SERVER_INSTRUCTIONS if surface == "admin" else None,
    )
    return _configure(stdio_mcp, surface=surface)
