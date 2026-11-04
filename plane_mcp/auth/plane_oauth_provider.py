"""Plane OAuth provider for FastMCP.

FastMCP's ``OAuthProxy`` runs the OAuth flow; this module points it at Plane
and keeps an MCP connection signed in for as long as it is used. The pieces
that make that hold live beside it:

- ``verifier``      — who a Plane token belongs to, tolerant of Plane blips
- ``plane_api``     — retrying Plane's token endpoint, 503 instead of invalid_grant
- ``refresh_grace`` — a rotated refresh token replayed moments later still works

Example:
    ```python
    from fastmcp import FastMCP
    from plane_mcp.auth import PlaneOAuthProvider

    auth = PlaneOAuthProvider(
        client_id="your-plane-client-id",
        client_secret="your-plane-client-secret",
        base_url="https://my-server.com",
    )
    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

import os

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT
from key_value.aio.protocols import AsyncKeyValue
from mcp.server.auth.provider import RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.routing import Route

from plane_mcp.auth.plane_api import (
    TOKEN_REQUEST_TIMEOUT_SECONDS,
    PlaneTokenClient,
    PlaneUnavailable,
    ServiceUnavailableWhilePlaneIsDown,
)
from plane_mcp.auth.refresh_grace import RefreshGrace, RotatedRefreshToken
from plane_mcp.auth.verifier import DEFAULT_PLANE_BASE_URL, PlaneOAuthTokenVerifier

logger = get_logger(__name__)

# How long a connection stays signed in without the user signing in again.
# Every refresh restarts the window, so a connection in use never expires.
# Plane's own refresh tokens never expire; 90 days matches Plane's web sessions.
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 90 * 24 * 60 * 60
# The access token handed to the MCP client. It is a reference to Plane's
# 10-hour token and outlives it: the proxy renews Plane's token behind it, so
# clients refresh (and risk a failed refresh) once a day instead of every 10 h.
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 24 * 60 * 60
# Renew Plane's token when a request lands this close to its expiry.
UPSTREAM_REFRESH_THRESHOLD_SECONDS = 60

# Name FastMCP's consent flow gives the cookie that remembers a Deny (it lives for a year).
_DENIED_CLIENTS_COOKIE = "MCP_DENIED_CLIENTS"


class PlaneOAuthProviderSettings(BaseSettings):
    """Settings for Plane OAuth provider."""

    model_config = SettingsConfigDict(
        env_prefix="PLANE_OAUTH_PROVIDER_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    client_id: str | None = None
    client_secret: SecretStr | None = None
    base_url: AnyHttpUrl | str | None = None
    issuer_url: AnyHttpUrl | str | None = None
    redirect_path: str | None = None
    required_scopes: list[str] | None = None
    timeout_seconds: int | None = None
    allowed_client_redirect_uris: list[str] | None = None
    jwt_signing_key: str | None = None
    plane_base_url: str | None = None
    plane_internal_base_url: str | None = None  # Internal URL for server-to-server calls
    enable_cimd: bool = False
    refresh_token_ttl_seconds: int = Field(default=DEFAULT_REFRESH_TOKEN_TTL_SECONDS, gt=0)
    access_token_ttl_seconds: int = Field(default=DEFAULT_ACCESS_TOKEN_TTL_SECONDS, gt=0)

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class PlaneOAuthProvider(OAuthProxy):
    """FastMCP's OAuth proxy, pointed at Plane and hardened against forced re-sign-ins."""

    def __init__(
        self,
        *,
        client_id: str | NotSetT = NotSet,
        client_secret: str | NotSetT = NotSet,
        base_url: AnyHttpUrl | str | NotSetT = NotSet,
        issuer_url: AnyHttpUrl | str | NotSetT = NotSet,
        redirect_path: str | NotSetT = NotSet,
        required_scopes: list[str] | NotSetT = NotSet,
        timeout_seconds: int | NotSetT = NotSet,
        allowed_client_redirect_uris: list[str] | NotSetT = NotSet,
        client_storage: AsyncKeyValue | None = None,
        jwt_signing_key: str | bytes | NotSetT = NotSet,
        require_authorization_consent: bool = True,
        plane_base_url: str | NotSetT = NotSet,
        plane_internal_base_url: str | NotSetT = NotSet,
        enable_cimd: bool | NotSetT = NotSet,
        refresh_token_ttl_seconds: int | NotSetT = NotSet,
        access_token_ttl_seconds: int | NotSetT = NotSet,
    ):
        """Initialize Plane OAuth provider.

        Args:
            client_id: Plane OAuth app client ID
            client_secret: Plane OAuth app client secret
            base_url: Public URL where OAuth endpoints will be accessible
                (includes any mount path)
            issuer_url: Issuer URL for OAuth metadata (defaults to base_url).
                Use root-level URL to avoid 404s during discovery when mounting
                under a path.
            redirect_path: Redirect path configured in Plane OAuth app
                (defaults to "/auth/callback")
            required_scopes: Required Plane scopes
                (currently not enforced by Plane API)
            timeout_seconds: HTTP request timeout for Plane API calls
            allowed_client_redirect_uris: List of allowed redirect URI patterns
                for MCP clients. If None (default), all URIs are allowed.
                If empty list, no URIs are allowed.
            client_storage: Storage backend for OAuth state
                (client registrations, encrypted tokens). If None, a DiskStore
                will be created in the data directory (derived from
                `platformdirs`). The disk store will be encrypted using a key
                derived from the JWT Signing Key.
            jwt_signing_key: Secret for signing FastMCP JWT tokens
                (any string or bytes). If bytes are provided, they will be used
                as is. If a string is provided, it will be derived into a
                32-byte key. If not provided, the upstream client secret will be
                used to derive a 32-byte key using PBKDF2 — so rotating that
                secret signs every connection out. Set it explicitly in
                production (PLANE_OAUTH_PROVIDER_JWT_SIGNING_KEY).
            require_authorization_consent: Whether to require user consent
                before authorizing clients (default True). When True, users see
                a consent screen before being redirected to Plane. When False,
                authorization proceeds directly without user confirmation.
                SECURITY WARNING: Only disable for local development or
                testing environments.
            plane_base_url: Base URL for Plane API
                (defaults to https://api.plane.so or PLANE_BASE_URL env var)
            enable_cimd: Whether to enable CIMD (Client ID Metadata Document) support.
                Defaults to False. Can be set via the PLANE_OAUTH_PROVIDER_ENABLE_CIMD environment variable.
            refresh_token_ttl_seconds: How long a connection stays signed in
                without the user signing in again; every refresh restarts it.
                Defaults to 90 days (PLANE_OAUTH_PROVIDER_REFRESH_TOKEN_TTL_SECONDS).
            access_token_ttl_seconds: Lifetime of the access token issued to the
                MCP client; the Plane token behind it is renewed transparently.
                Defaults to 24 hours (PLANE_OAUTH_PROVIDER_ACCESS_TOKEN_TTL_SECONDS).
        """

        settings = PlaneOAuthProviderSettings.model_validate(
            {
                k: v
                for k, v in {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "base_url": base_url,
                    "issuer_url": issuer_url,
                    "redirect_path": redirect_path,
                    "required_scopes": required_scopes,
                    "timeout_seconds": timeout_seconds,
                    "allowed_client_redirect_uris": allowed_client_redirect_uris,
                    "jwt_signing_key": jwt_signing_key,
                    "plane_base_url": plane_base_url,
                    "plane_internal_base_url": plane_internal_base_url,
                    "enable_cimd": enable_cimd,
                    "refresh_token_ttl_seconds": refresh_token_ttl_seconds,
                    "access_token_ttl_seconds": access_token_ttl_seconds,
                }.items()
                if v is not NotSet
            }
        )

        # Validate required settings
        if not settings.client_id:
            raise ValueError("client_id is required - set via parameter or PLANE_OAUTH_PROVIDER_CLIENT_ID")
        if not settings.client_secret:
            raise ValueError("client_secret is required - set via parameter or PLANE_OAUTH_PROVIDER_CLIENT_SECRET")

        # Apply defaults
        timeout_seconds_final = settings.timeout_seconds or 10
        required_scopes_final = settings.required_scopes or []
        allowed_client_redirect_uris_final = settings.allowed_client_redirect_uris
        plane_base_url_final = settings.plane_base_url or os.getenv("PLANE_BASE_URL", DEFAULT_PLANE_BASE_URL)
        # Internal URL for server-to-server calls (token exchange, API verification)
        # Falls back to external URL if not set
        plane_internal_url = (
            settings.plane_internal_base_url or os.getenv("PLANE_INTERNAL_BASE_URL") or plane_base_url_final
        )

        # Create Plane token verifier (uses internal URL for server-to-server calls)
        token_verifier = PlaneOAuthTokenVerifier(
            required_scopes=required_scopes_final,
            timeout_seconds=timeout_seconds_final,
            plane_base_url=plane_internal_url,
        )

        # Extract secret string from SecretStr
        client_secret_str = settings.client_secret.get_secret_value() if settings.client_secret else ""

        # Initialize OAuth proxy with Plane endpoints
        # Authorization: external URL (user's browser)
        # Token exchange: internal URL (server-to-server)
        super().__init__(
            upstream_authorization_endpoint=f"{plane_base_url_final}/auth/o/authorize-app/",
            upstream_token_endpoint=f"{plane_internal_url}/auth/o/token/",
            upstream_client_id=settings.client_id,
            upstream_client_secret=client_secret_str,
            token_verifier=token_verifier,
            base_url=settings.base_url,
            redirect_path=settings.redirect_path,
            issuer_url=settings.issuer_url or settings.base_url,  # Default to base_url if not specified
            allowed_client_redirect_uris=allowed_client_redirect_uris_final,
            client_storage=client_storage,
            jwt_signing_key=settings.jwt_signing_key,
            require_authorization_consent=require_authorization_consent,
            valid_scopes=["read", "write"],
            enable_cimd=settings.enable_cimd,
            fallback_refresh_token_expiry_seconds=settings.refresh_token_ttl_seconds,
            fastmcp_access_token_expiry_seconds=settings.access_token_ttl_seconds,
            token_expiry_threshold_seconds=UPSTREAM_REFRESH_THRESHOLD_SECONDS,
        )

        self._refresh_token_ttl_seconds = settings.refresh_token_ttl_seconds
        self._refresh_grace = RefreshGrace(self._client_storage)

        logger.info(
            "Initialized Plane OAuth provider for client %s with scopes: %s",
            settings.client_id,
            required_scopes_final,
        )

    def _decode_list_cookie(self, request: Request, base_name: str) -> list[str]:
        """Ignore FastMCP's year-long denied-clients cookie so a Deny only applies to that attempt."""
        if base_name == _DENIED_CLIENTS_COOKIE:
            return []
        return super()._decode_list_cookie(request, base_name)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """FastMCP's routes, with the token endpoint answering 503 while Plane is down."""
        return [
            Route(
                route.path,
                ServiceUnavailableWhilePlaneIsDown(route.endpoint),
                methods=sorted(route.methods or []),
                name=route.name,
            )
            if isinstance(route, Route) and route.path == "/token"
            else route
            for route in super().get_routes(mcp_path)
        ]

    def _create_upstream_oauth_client(self) -> AsyncOAuth2Client:
        secret = self._upstream_client_secret
        return PlaneTokenClient(
            client_id=self._upstream_client_id,
            client_secret=secret.get_secret_value() if secret is not None else None,
            token_endpoint_auth_method=self._token_endpoint_auth_method,
            timeout=TOKEN_REQUEST_TIMEOUT_SECONDS,
            refresh_expires_in=self._refresh_token_ttl_seconds,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        loaded = await super().load_refresh_token(client, refresh_token)
        if loaded is not None:
            return loaded
        # Rotated already, or being rotated by a concurrent refresh in this
        # process: waiting for its lock lets that refresh record the new pair.
        async with self._refresh_grace.lock(refresh_token):
            rotated = await self._refresh_grace.get(refresh_token)
        if rotated is None or rotated.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=rotated.client_id,
            scopes=(rotated.tokens.scope or "").split(),
            expires_at=int(rotated.grace_ends_at),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        async with self._refresh_grace.lock(refresh_token.token):
            if rotated := await self._refresh_grace.get(refresh_token.token):
                return self._replay(rotated, client)
            try:
                tokens = await super().exchange_refresh_token(client, refresh_token, scopes)
            except TokenError as exc:
                # FastMCP reports every upstream failure as invalid_grant; an outage is not one.
                if isinstance(exc.__cause__, (PlaneUnavailable, httpx.TransportError)):
                    raise PlaneUnavailable(str(exc.__cause__)) from exc.__cause__
                # Another replica may have rotated this token a moment ago.
                if rotated := await self._refresh_grace.get(refresh_token.token, settle_seconds=0.5):
                    return self._replay(rotated, client)
                raise
            if tokens.refresh_token:
                await self._refresh_grace.record(refresh_token.token, client.client_id, tokens)
            return tokens

    @staticmethod
    def _replay(rotated: RotatedRefreshToken, client: OAuthClientInformationFull) -> OAuthToken:
        # load_refresh_token already filters by client; checked again because a pair is being handed out.
        if rotated.client_id != client.client_id:
            raise TokenError("invalid_grant", "Refresh token was issued to another client")
        logger.info("Rotated refresh token presented again; answering with the same token pair")
        return rotated.replay()
