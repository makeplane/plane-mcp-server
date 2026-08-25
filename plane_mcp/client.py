"""Plane client initialization for MCP server."""

import os
from typing import NamedTuple

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.logging import get_logger
from plane import PlaneClient

logger = get_logger(__name__)


class PlaneClientContext(NamedTuple):
    """Context containing Plane client and workspace information."""

    client: PlaneClient
    workspace_slug: str


def _resolve_ssl_verify(base_url: str) -> bool | str:
    """
    Resolve TLS verification mode from PLANE_SSL_VERIFY.

    - Unset (default): verify=True, normal certificate verification.
    - "false"/"0"/"no" (case-insensitive): verify=False, disable verification
      entirely. Logs a warning on every call since this drops protection
      against a man-in-the-middle.
    - Any other value that names an existing file path: treated as a CA
      bundle and passed through verbatim.
    - Anything else: verify=True (fail safe rather than silently misparse).
    """
    raw = os.environ.get("PLANE_SSL_VERIFY")
    if not raw:
        return True

    if raw.strip().lower() in ("false", "0", "no"):
        logger.warning(
            "TLS verification is DISABLED for %s (PLANE_SSL_VERIFY=%s). "
            "The connection has no protection against a man-in-the-middle; "
            "requests, responses, and the API key are exposed to anyone "
            "positioned on the network path.",
            base_url,
            raw,
        )
        return False

    if os.path.exists(raw):
        return raw

    logger.warning(
        "PLANE_SSL_VERIFY=%s is neither false/0/no nor an existing file path; "
        "falling back to verify=True.",
        raw,
    )
    return True


def get_plane_client_context() -> PlaneClientContext:
    """
    Initialize and return a PlaneClient instance with workspace context.

    Authentication is handled by the PlaneOAuthProvider, which supports:
    1. Environment variables (PLANE_API_KEY + PLANE_WORKSPACE_SLUG)
    2. HTTP headers (x-api-key + x-workspace-slug)
    3. OAuth access token

    Environment variables:
    - PLANE_INTERNAL_BASE_URL: Internal URL for Plane API (preferred for server-to-server calls)
    - PLANE_BASE_URL: Base URL for Plane API (fallback, default: https://api.plane.so)
    - PLANE_SSL_VERIFY: TLS verification mode. Unset/default = verify normally.
      "false"/"0"/"no" = disable verification entirely (logs a warning on every
      use). A path to an existing file = treated as a CA bundle.

    Returns:
        PlaneClientContext containing configured PlaneClient instance and workspace slug

    Raises:
        ConfigurationError: If access token is not available or workspace slug is missing
    """
    base_url = os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL", "https://api.plane.so")
    workspace_slug = os.getenv("PLANE_WORKSPACE_SLUG", "")

    api_key = os.getenv("PLANE_API_KEY", "")
    access_token = None

    # Get access token from the OAuth provider (which handles all auth methods)
    stored_access_token: AccessToken | None = get_access_token()
    if stored_access_token:
        # Determine authentication method to use appropriate PlaneClient constructor
        auth_method = stored_access_token.claims.get("auth_method", "oauth")
        token = stored_access_token.token
        workspace_slug = stored_access_token.claims.get("workspace_slug", "")

        # For API key auth methods, use api_key parameter; for OAuth, use access_token
        if auth_method in ("api_key_env", "api_key_header"):
            api_key = token
        else:
            access_token = token

    verify = _resolve_ssl_verify(base_url)

    if access_token:
        client = PlaneClient(
            base_url=base_url,
            access_token=access_token,
            verify=verify,
        )
    else:
        client = PlaneClient(
            base_url=base_url,
            api_key=api_key,
            verify=verify,
        )

    return PlaneClientContext(
        client=client,
        workspace_slug=workspace_slug,
    )
