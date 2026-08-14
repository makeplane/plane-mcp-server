"""Plane OAuth provider for FastMCP.

This module provides a complete Plane OAuth integration that's ready to use
with just a client ID and client secret. It handles all the complexity of
Plane's OAuth flow, token validation, and user management.

Example:
    ```python
    from fastmcp import FastMCP
    from plane_mcp.plane_oauth_provider import PlaneOAuthProvider

    # Simple Plane OAuth protection
    auth = PlaneOAuthProvider(
        client_id="your-plane-client-id",
        client_secret="your-plane-client-secret",
        base_url="https://api.plane.so"
    )

    mcp = FastMCP("My Protected Server", auth=auth)
    ```
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass

import httpx
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.settings import ENV_FILE
from fastmcp.utilities.auth import parse_scopes
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import NotSet, NotSetT
from key_value.aio.protocols import AsyncKeyValue
from plane.models.users import UserLite
from pydantic import AnyHttpUrl, BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = get_logger(__name__)

# When true, user info (PII such as the display name) is included in logs.
# Defaults to false so PII is never logged unless explicitly opted in.
LOG_USER_INFO: bool = os.getenv("LOG_USER_INFO", "").lower() == "true"


DEFAULT_PLANE_BASE_URL = "https://api.plane.so"

# Successful verifications are cached this long — a burst of MCP requests must
# not become a burst of Plane API calls. Worst-case revocation latency = this TTL.
VERIFY_CACHE_TTL_SECONDS = int(os.getenv("PLANE_VERIFY_CACHE_TTL_SECONDS", "60"))

# When Plane is unreachable (timeout, 5xx, 429, deploy blip), a previously
# verified token is served from cache for up to this long instead of being
# reported as invalid — transient upstream failures must not flip connectors
# to "needs authentication".
VERIFY_STALE_TTL_SECONDS = int(os.getenv("PLANE_VERIFY_STALE_TTL_SECONDS", "900"))

# Bound on the in-memory verification cache.
VERIFY_CACHE_MAX_ENTRIES = 1024

# Plane responses that definitively reject a token. Everything else that isn't
# a 200 is treated as transient (5xx, 429, ...) and must never revoke auth.
DEFINITIVE_REJECTION_STATUSES = frozenset({401, 403})

# Refresh the upstream Plane token this many seconds before it actually
# expires. Without a margin, every session/replica discovers expiry at the
# same instant and races the refresh — with rotation enabled, the losers get
# invalid_grant and force the user through interactive re-auth.
DEFAULT_TOKEN_EXPIRY_THRESHOLD_SECONDS = 300


class TransientVerificationError(Exception):
    """Verification could not be completed — says nothing about token validity.

    Raised for timeouts, connection errors, 429s, 5xxs, and unparseable
    responses. Must never be treated as "token invalid".
    """


@dataclass
class _CacheEntry:
    access_token: AccessToken
    verified_at: float


class _VerificationCache:
    """Bounded in-memory cache of successful token verifications.

    Keys are SHA-256 hashes of the raw token, so bearer tokens are never held
    as dict keys. A single stored entry serves both the fresh window and the
    stale-if-error window — callers choose the max age per lookup.
    """

    def __init__(self, *, max_entries: int, evict_after_seconds: float) -> None:
        self._max_entries = max_entries
        self._evict_after_seconds = evict_after_seconds
        self._entries: dict[str, _CacheEntry] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def get(self, token: str, *, max_age_seconds: float) -> AccessToken | None:
        entry = self._entries.get(self._key(token))
        if entry and (time.time() - entry.verified_at) <= max_age_seconds:
            return entry.access_token.model_copy(deep=True)
        return None

    def put(self, token: str, access_token: AccessToken) -> None:
        if len(self._entries) >= self._max_entries:
            self._evict()
        self._entries[self._key(token)] = _CacheEntry(access_token=access_token, verified_at=time.time())

    def discard(self, token: str) -> None:
        """Drop any entry so a rejected token cannot be served stale later."""
        self._entries.pop(self._key(token), None)

    def _evict(self) -> None:
        # Drop entries past the stale window first, then the oldest.
        now = time.time()
        for key in [k for k, v in self._entries.items() if now - v.verified_at > self._evict_after_seconds]:
            del self._entries[key]
        if len(self._entries) >= self._max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].verified_at)
            del self._entries[oldest]


class WorkspaceDetail(BaseModel):
    """Workspace detail information."""

    name: str
    slug: str
    id: str
    logo_url: str | None = None


class PlaneOAuthAppInstallation(BaseModel):
    """Plane OAuth app installation information."""

    id: str
    workspace_detail: WorkspaceDetail
    created_at: str
    updated_at: str
    deleted_at: str | None = None
    status: str
    created_by: str | None = None
    updated_by: str | None = None
    workspace: str
    application: str
    installed_by: str
    app_bot: str
    webhook: str | None = None


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
    token_expiry_threshold_seconds: int | None = None
    access_token_expiry_seconds: int | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        return parse_scopes(v)


class PlaneOAuthTokenVerifier(TokenVerifier):
    """Token verifier for Plane OAuth tokens.

    Plane OAuth tokens are verified by calling Plane's API. Two failure classes
    are kept strictly apart:

    - **Definitive**: Plane answered 401/403 (or the app has no installation) —
      the token is invalid, return ``None`` so the caller responds 401.
    - **Transient**: Plane could not be reached or answered 5xx/429 — the token
      may be perfectly valid. Serve the last successful verification from cache
      (up to ``VERIFY_STALE_TTL_SECONDS``), retry once, and only then give up.

    Successful verifications are cached for ``VERIFY_CACHE_TTL_SECONDS`` so a
    burst of MCP requests does not become a burst of Plane API calls.
    """

    def __init__(
        self,
        *,
        required_scopes: list[str] | None = None,
        timeout_seconds: int = 10,
        plane_base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay_seconds: float = 0.5,
        cache_ttl_seconds: float | None = None,
        stale_ttl_seconds: float | None = None,
    ):
        """Initialize the Plane token verifier.

        Args:
            required_scopes: Required OAuth scopes (currently not enforced by Plane API)
            timeout_seconds: HTTP request timeout
            plane_base_url: Base URL for Plane API (defaults to https://api.plane.so)
            transport: Optional httpx transport override (used by tests)
            retry_delay_seconds: Delay before the single retry on transient failure
            cache_ttl_seconds: How long a successful verification is served without
                revalidating (defaults to ``VERIFY_CACHE_TTL_SECONDS``)
            stale_ttl_seconds: How long a previous successful verification may be
                served when Plane is unavailable (defaults to ``VERIFY_STALE_TTL_SECONDS``)
        """
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds
        self.plane_base_url = plane_base_url or os.getenv("PLANE_BASE_URL", DEFAULT_PLANE_BASE_URL)
        self._transport = transport
        self._retry_delay_seconds = retry_delay_seconds
        self._cache_ttl_seconds = VERIFY_CACHE_TTL_SECONDS if cache_ttl_seconds is None else cache_ttl_seconds
        self._stale_ttl_seconds = VERIFY_STALE_TTL_SECONDS if stale_ttl_seconds is None else stale_ttl_seconds
        self._cache = _VerificationCache(
            max_entries=VERIFY_CACHE_MAX_ENTRIES,
            evict_after_seconds=self._stale_ttl_seconds,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a Plane OAuth token, with caching and transient-failure grace."""
        if not token:
            return None

        cached = self._cache.get(token, max_age_seconds=self._cache_ttl_seconds)
        if cached is not None:
            logger.debug("verify_token cache hit")
            return cached

        try:
            access_token = await self._verify_upstream(token)
        except TransientVerificationError as exc:
            stale = self._cache.get(token, max_age_seconds=self._stale_ttl_seconds)
            if stale is not None:
                logger.warning("Plane API unavailable during verification (%s) — serving cached verification", exc)
                return stale
            # No cache to fall back on — retry once before giving up.
            logger.warning("Plane API unavailable during verification (%s) — retrying once", exc)
            await asyncio.sleep(self._retry_delay_seconds)
            try:
                access_token = await self._verify_upstream(token)
            except TransientVerificationError as retry_exc:
                logger.error(
                    "Plane token verification unavailable after retry (%s) — request will fail with 401",
                    retry_exc,
                )
                return None

        if access_token is None:
            # Definitive rejection — make sure no stale entry can resurrect it.
            self._cache.discard(token)
            return None

        self._cache.put(token, access_token)
        return access_token

    async def _verify_upstream(self, token: str) -> AccessToken | None:
        """Call Plane to verify the token.

        Returns the AccessToken on success, ``None`` when Plane definitively
        rejected the token, and raises :class:`TransientVerificationError` when
        the answer is unknowable right now.
        """
        base_url = self.plane_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            # A short-lived client per verification is deliberate: the success
            # cache makes upstream calls rare, and a pooled client would tie
            # connections to a single event loop's lifetime.
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = await client.get(f"{base_url}/api/v1/users/me/", headers=headers)

                logger.info(f"Plane API response status: {response.status_code}")
                if response.status_code in DEFINITIVE_REJECTION_STATUSES:
                    logger.info(
                        "Plane token definitively rejected: %s - %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None
                if response.status_code != 200:
                    raise TransientVerificationError(f"/users/me/ returned {response.status_code}")

                user_data = response.json()
                user = UserLite.model_validate(user_data)

                # display_name is PII — only log it when explicitly opted in.
                if LOG_USER_INFO:
                    logger.info(f"User verified: ({user.id}) - {user.display_name}")
                else:
                    logger.info(f"User verified: ({user.id})")

                installations_response = await client.get(f"{base_url}/auth/o/app-installation/", headers=headers)

                if installations_response.status_code in DEFINITIVE_REJECTION_STATUSES:
                    logger.info(
                        "App installation lookup definitively rejected: %s",
                        installations_response.status_code,
                    )
                    return None
                if installations_response.status_code != 200:
                    raise TransientVerificationError(
                        f"/auth/o/app-installation/ returned {installations_response.status_code}"
                    )

                installations = installations_response.json()
                if not isinstance(installations, list):
                    raise TransientVerificationError("/auth/o/app-installation/ returned a non-list payload")
                if not installations:
                    # Genuine state: the MCP app is not installed in any
                    # workspace for this token — nothing to serve.
                    logger.info("No app installations found for token — treating as invalid")
                    return None

                workspace_detail = installations[0].get("workspace_detail") or {}

                return AccessToken(
                    token=token,
                    client_id=user.id or "unknown",
                    scopes=["read", "write"],  # Plane doesn't expose scopes in user endpoint
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
                        "workspace_slug": workspace_detail.get("slug"),
                        "workspace": workspace_detail,
                    },
                )

        except TransientVerificationError:
            raise
        except httpx.RequestError as e:
            # Timeouts, DNS failures, connection resets — all transient.
            raise TransientVerificationError(f"request error: {e}") from e
        except Exception as e:
            # Unparseable payloads and other surprises say nothing about the
            # token itself — never convert them into an auth failure.
            logger.info(f"Unexpected error verifying Plane token: {e}", exc_info=True)
            raise TransientVerificationError(f"unexpected error: {type(e).__name__}: {e}") from e


class PlaneOAuthProvider(OAuthProxy):
    """Complete Plane OAuth provider for FastMCP.

    This provider makes it trivial to add Plane OAuth protection to any
    FastMCP server. Just provide your Plane OAuth app credentials and
    a base URL, and you're ready to go.

    Features:
    - Transparent OAuth proxy to Plane
    - Automatic token validation via Plane API
    - User information extraction
    - Minimal configuration required

    Example:
        ```python
        from fastmcp import FastMCP
        from plane_mcp.plane_oauth_provider import PlaneOAuthProvider

        auth = PlaneOAuthProvider(
            client_id="your-client-id",
            client_secret="your-client-secret",
            base_url="https://my-server.com",
            plane_base_url="https://api.plane.so"
        )

        mcp = FastMCP("My App", auth=auth)
        ```
    """

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
        token_expiry_threshold_seconds: int | NotSetT = NotSet,
        access_token_expiry_seconds: int | NotSetT = NotSet,
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
                used to derive a 32-byte key using PBKDF2.
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
            token_expiry_threshold_seconds: Refresh the upstream Plane token this
                many seconds before expiry (default 300). Avoids concurrent
                sessions racing the refresh at the expiry boundary. Env:
                PLANE_OAUTH_PROVIDER_TOKEN_EXPIRY_THRESHOLD_SECONDS.
            access_token_expiry_seconds: Lifetime of the FastMCP-issued access
                token, decoupled from the upstream Plane token's expires_in.
                Unset mirrors the upstream lifetime. Safe to raise: the FastMCP
                JWT is a reference token — the upstream token is still validated
                on every request, so revocation is unaffected. Env:
                PLANE_OAUTH_PROVIDER_ACCESS_TOKEN_EXPIRY_SECONDS.
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
                    "token_expiry_threshold_seconds": token_expiry_threshold_seconds,
                    "access_token_expiry_seconds": access_token_expiry_seconds,
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
            # Proactive upstream refresh: avoid the expiry-boundary refresh race.
            token_expiry_threshold_seconds=(
                settings.token_expiry_threshold_seconds
                if settings.token_expiry_threshold_seconds is not None
                else DEFAULT_TOKEN_EXPIRY_THRESHOLD_SECONDS
            ),
            # None mirrors the upstream token lifetime (current behavior).
            fastmcp_access_token_expiry_seconds=settings.access_token_expiry_seconds,
        )

        logger.info(
            "Initialized Plane OAuth provider for client %s with scopes: %s",
            settings.client_id,
            required_scopes_final,
        )
