"""Plane client initialization for MCP server."""

import os
from typing import NamedTuple

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.logging import get_logger
from plane import PlaneClient
from plane.errors import ConfigurationError

logger = get_logger(__name__)


class PlaneClientContext(NamedTuple):
    """Context containing Plane client and workspace information."""

    client: PlaneClient
    workspace_slug: str


def get_plane_client_context() -> PlaneClientContext:
    """
    Initialize and return a PlaneClient instance with workspace context.

    Reads configuration from environment variables:
    - PLANE_BASE_URL: Base URL for Plane API (default: https://api.plane.so)
    - PLANE_API_KEY: API key for authentication
    - PLANE_ACCESS_TOKEN: Access token for authentication
    - PLANE_WORKSPACE_SLUG: Workspace slug (required for API key flow)

    Returns:
        PlaneClientContext containing configured PlaneClient instance and workspace slug

    Raises:
        ConfigurationError: If required configuration is missing
    """
    base_url = os.getenv("PLANE_BASE_URL", "https://api.plane.so")
    api_key = os.getenv("PLANE_API_KEY")
    access_token: str | None = None
    workspace_slug: str | None = None

    # use PAT token flow if API key is provided
    if api_key:
        workspace_slug = os.getenv("PLANE_WORKSPACE_SLUG")
        if not workspace_slug:
            raise ConfigurationError(
                "PLANE_WORKSPACE_SLUG environment variable must be set when using API key"
            )
        return PlaneClientContext(
            client=PlaneClient(base_url=base_url, api_key=api_key),
            workspace_slug=workspace_slug,
        )

    # Use OAuth token flow if API key is not provided
    stored_access_token: AccessToken | None = get_access_token()
    if stored_access_token:
        access_token = stored_access_token.token
        workspace_slug = stored_access_token.claims.get("workspace_slug")
        if not workspace_slug:
            raise ConfigurationError(
                "Workspace slug not found in access token claims"
            )

    if not access_token:
        raise ConfigurationError(
            "Either PLANE_API_KEY or PLANE_ACCESS_TOKEN environment variable must be set"
        )

    return PlaneClientContext(
        client=PlaneClient(base_url=base_url, access_token=access_token),
        workspace_slug=workspace_slug,
    )
