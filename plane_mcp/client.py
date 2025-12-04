"""Plane client initialization for MCP server."""

import os

from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.logging import get_logger
from plane import PlaneClient
from plane.errors import ConfigurationError

logger = get_logger(__name__)


def get_plane_client() -> PlaneClient:
    """
    Initialize and return a PlaneClient instance.
    
    Reads configuration from environment variables:
    - PLANE_BASE_URL: Base URL for Plane API (default: https://api.plane.so)
    - PLANE_API_KEY: API key for authentication
    - PLANE_ACCESS_TOKEN: Access token for authentication
    
    Returns:
        Configured PlaneClient instance
        
    Raises:
        ConfigurationError: If required configuration is missing
    """
    base_url = os.getenv("PLANE_BASE_URL", "https://api.plane.so")
    api_key = os.getenv("PLANE_API_KEY")
    access_token = os.getenv("PLANE_ACCESS_TOKEN")

    stored_access_token = get_access_token()
    if stored_access_token:
        access_token = stored_access_token.token

    if not api_key and not access_token:
        raise ConfigurationError(
            "Either PLANE_API_KEY or PLANE_ACCESS_TOKEN environment variable must be set"
        )
    
    return PlaneClient(
        base_url=base_url,
        api_key=api_key,
        access_token=access_token,
    )

