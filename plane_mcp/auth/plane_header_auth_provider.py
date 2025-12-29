import time

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class PlaneHeaderAuthProvider(TokenVerifier):
    def __init__(self, required_scopes: list[str] | None = None):
        super().__init__(required_scopes=required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            from fastmcp.server.dependencies import get_http_headers

            headers = get_http_headers()

            if token:
                workspace_slug = headers.get("x-workspace-slug")
                if workspace_slug:
                    logger.info("Using API key from HTTP headers")
                    expires_at = int(time.time() + 3600)
                    return AccessToken(
                        token=token,
                        client_id="api_key_header_user",
                        scopes=["read", "write"],
                        expires_at=expires_at,
                        claims={
                            "auth_method": "api_key_header",
                            "workspace_slug": workspace_slug,
                        },
                    )
                else:
                    logger.warning("x-api-key header found but x-workspace-slug is missing")
        except RuntimeError:
            # No active HTTP request available (e.g., stdio transport)
            logger.debug("No active HTTP request available for header check")
