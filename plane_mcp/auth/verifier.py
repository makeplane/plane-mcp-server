"""Verifying a Plane OAuth access token by asking Plane who it belongs to."""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.logging import get_logger
from plane.models.users import UserLite

from plane_mcp.auth.plane_api import PlaneUnavailable, raise_if_unavailable

logger = get_logger(__name__)

# When true, user info (PII such as the display name) is included in logs.
LOG_USER_INFO: bool = os.getenv("LOG_USER_INFO", "").lower() == "true"

DEFAULT_PLANE_BASE_URL = "https://api.plane.so"

# Plane is asked about a token at most once per FRESH window. While Plane is
# unavailable, its last answer is trusted for up to STALE seconds, so a blip on
# Plane's side does not sign every connection out. A refusal is never cached.
FRESH_SECONDS = 60
STALE_SECONDS = 10 * 60
CACHE_SIZE = 10_000


class PlaneOAuthTokenVerifier(TokenVerifier):
    """Verifies Plane OAuth tokens against Plane's API, caching Plane's answers briefly."""

    def __init__(
        self,
        *,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        plane_base_url: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds
        self.plane_base_url = plane_base_url or os.getenv("PLANE_BASE_URL", DEFAULT_PLANE_BASE_URL)
        self._clock = clock
        self._verified: OrderedDict[str, tuple[AccessToken, float]] = OrderedDict()

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_seconds)

    async def verify_token(self, token: str) -> AccessToken | None:
        now = self._clock()
        cached, verified_at = self._verified.get(token, (None, 0.0))
        if cached is not None and now - verified_at < FRESH_SECONDS:
            return cached.model_copy(deep=True)

        try:
            verified = await self._ask_plane(token)
        except PlaneUnavailable as exc:
            if cached is not None and now - verified_at < STALE_SECONDS:
                logger.warning("Plane unavailable (%s); trusting its answer from %.0fs ago", exc, now - verified_at)
                return cached.model_copy(deep=True)
            logger.warning("Plane unavailable (%s); token cannot be verified", exc)
            return None

        if verified is None:
            self._verified.pop(token, None)
            return None
        self._verified[token] = (verified, now)
        self._verified.move_to_end(token)
        if len(self._verified) > CACHE_SIZE:
            self._verified.popitem(last=False)
        return verified.model_copy(deep=True)

    async def _ask_plane(self, token: str) -> AccessToken | None:
        """The token's owner and workspace; None if Plane refuses it, PlaneUnavailable if Plane cannot answer."""
        base_url = self.plane_base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            async with self._http_client() as client:
                user_response = await client.get(f"{base_url}/api/v1/users/me/", headers=headers)
                raise_if_unavailable(user_response)
                if user_response.status_code != 200:
                    logger.info("Plane refused the token: %s %s", user_response.status_code, user_response.text[:200])
                    return None
                installations_response = await client.get(f"{base_url}/auth/o/app-installation/", headers=headers)
                raise_if_unavailable(installations_response)
        except httpx.RequestError as exc:
            raise PlaneUnavailable(f"request to Plane failed: {exc}") from exc

        try:
            user_data = user_response.json()
            user = UserLite.model_validate(user_data)
            installations: list[dict[str, Any]] = (
                installations_response.json() if installations_response.status_code == 200 else []
            )
        except Exception as exc:
            # Plane said yes but sent a body we cannot read: its fault, not the token's.
            raise PlaneUnavailable(f"unreadable answer from Plane: {exc}") from exc
        if not installations:
            logger.info("Token has no app installation (HTTP %s)", installations_response.status_code)
            return None

        if LOG_USER_INFO:  # display_name is PII — logged only when opted in.
            logger.info("User verified: (%s) - %s", user.id, user.display_name)
        else:
            logger.info("User verified: (%s)", user.id)

        workspace = installations[0].get("workspace_detail", {})
        return AccessToken(
            token=token,
            client_id=user.id or "unknown",
            scopes=["read", "write"],  # Plane doesn't expose scopes in the user endpoint
            expires_at=int(time.time() + 3600),
            claims={
                "auth_method": "oauth",
                "sub": user.id or "unknown",
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "display_name": user.display_name,
                "avatar": user.avatar,
                "avatar_url": user.avatar_url,
                "plane_user_data": user_data,
                "workspace_slug": workspace.get("slug"),
                "workspace": workspace,
            },
        )
