"""Telling a Plane outage apart from Plane's verdict on a token.

Every MCP client treats a failed refresh as the end of its session and sends
the user back through sign-in. So the proxy must never turn "Plane is down,
throttled or unreachable" into "your token is bad": the first is retried and,
if it persists, answered with 503 so the client keeps its refresh token.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastmcp.utilities.logging import get_logger
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = get_logger(__name__)

# Plane answers its token endpoint in ~100 ms. The worst case below (three
# timed-out attempts plus pauses, ~16 s) stays inside the ~30 s an MCP client
# waits for /token: a client giving up counts as a failed refresh too.
TOKEN_REQUEST_TIMEOUT_SECONDS = 5
# Pauses between attempts (one more attempt than pauses). Retrying is safe:
# Plane keeps a rotated refresh token usable for its grace period (120 s) and
# answers a repeat with the same token pair.
RETRY_DELAYS_SECONDS = (0.5, 1.0)


class PlaneUnavailable(Exception):
    """Plane could not answer: 5xx, throttled (429) or unreachable. Says nothing about the token."""


def raise_if_unavailable(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        request = response.request
        raise PlaneUnavailable(f"{request.method} {request.url.path} answered HTTP {response.status_code}")


class PlaneTokenClient(AsyncOAuth2Client):
    """The OAuth client the proxy uses for Plane's token endpoint.

    Plane's refresh tokens never expire, so its token responses carry no
    ``refresh_expires_in``; each one is stamped with this server's refresh TTL,
    which FastMCP uses to restart the session window on every refresh.
    Refreshes that fail because Plane is unavailable are retried.
    """

    def __init__(self, *args: Any, refresh_expires_in: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._refresh_expires_in = refresh_expires_in

    def parse_response_token(self, resp: httpx.Response) -> dict[str, Any]:
        raise_if_unavailable(resp)
        token = super().parse_response_token(resp)
        if token.get("refresh_token"):
            token.setdefault("refresh_expires_in", self._refresh_expires_in)
        return token

    async def refresh_token(self, url: str | None = None, refresh_token: str | None = None, **kwargs: Any) -> Any:
        for delay in (*RETRY_DELAYS_SECONDS, None):
            try:
                return await super().refresh_token(url=url, refresh_token=refresh_token, **kwargs)
            except (PlaneUnavailable, httpx.TransportError) as exc:
                if delay is None:
                    raise
                logger.warning("Plane token refresh failed (%s); retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)


class ServiceUnavailableWhilePlaneIsDown:
    """Wraps the token endpoint: a Plane outage is answered 503 Retry-After, not ``invalid_grant``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        except PlaneUnavailable as exc:
            logger.warning("Token request not answered, Plane is unavailable: %s", exc)
            response = JSONResponse(
                {
                    "error": "temporarily_unavailable",
                    "error_description": "Plane is unavailable; the refresh token is still valid, retry shortly.",
                },
                status_code=503,
                headers={"Retry-After": "5", "Cache-Control": "no-store", "Pragma": "no-cache"},
            )
            await response(scope, receive, send)
