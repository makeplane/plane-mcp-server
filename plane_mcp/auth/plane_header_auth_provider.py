import os
import time

import httpx
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

DEFAULT_PLANE_BASE_URL = "https://api.plane.so"


class PlaneHeaderAuthProvider(TokenVerifier):
    def __init__(self, required_scopes: list[str] | None = None, timeout_seconds: int = 10):
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds
        self.plane_base_url = (
            os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL", DEFAULT_PLANE_BASE_URL)
        ).rstrip("/")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            from fastmcp.server.dependencies import get_http_headers

            headers = get_http_headers()

            if token:
                workspace_slug = headers.get("x-workspace-slug")
                if workspace_slug:
                    logger.info("Verifying API key against Plane API")
                    user_url = f"{self.plane_base_url}/api/v1/users/me/"
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.get(
                            user_url,
                            headers={
                                "x-api-key": token,
                                "Content-Type": "application/json",
                            },
                        )
                        if response.status_code != 200:
                            logger.warning("API key verification failed: %s", response.status_code)
                            return None

                    logger.info("API key verified successfully")
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
        except httpx.RequestError as e:
            logger.warning("API key verification request failed: %s", e)
            return None
        except RuntimeError:
            # No active HTTP request available (e.g., stdio transport)
            logger.debug("No active HTTP request available for header check")
