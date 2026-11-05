"""A grace window for rotated refresh tokens.

FastMCP rotates the refresh token on every refresh and forgets the old one at
once. When two processes of one MCP client (or a client retrying a response it
never received) refresh with the same token, the second gets ``invalid_grant``
and the user is sent back through sign-in. Within the window, a repeat is
answered with the pair the first refresh produced instead. Entries live in the
shared token store, so the window spans server replicas; a repeat that reaches
another replica while the first refresh is still in flight waits briefly for it.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

import anyio
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from mcp.shared.auth import OAuthToken
from pydantic import BaseModel

# Mirrors Plane's own REFRESH_TOKEN_GRACE_PERIOD_SECONDS.
GRACE_WINDOW_SECONDS = 120
LOCK_CACHE_SIZE = 10_000


class RotatedRefreshToken(BaseModel):
    """The token pair a refresh produced, kept under the refresh token it replaced."""

    client_id: str
    tokens: OAuthToken
    rotated_at: float

    @property
    def grace_ends_at(self) -> float:
        return self.rotated_at + GRACE_WINDOW_SECONDS

    def replay(self) -> OAuthToken:
        """The same pair, with ``expires_in`` counted from now."""
        elapsed = int(time.time() - self.rotated_at)
        return self.tokens.model_copy(update={"expires_in": max((self.tokens.expires_in or 0) - elapsed, 1)})


class RefreshGrace:
    def __init__(self, storage: AsyncKeyValue) -> None:
        self._store = PydanticAdapter[RotatedRefreshToken](
            key_value=storage,
            pydantic_model=RotatedRefreshToken,
            default_collection="plane-rotated-refresh-tokens",
            raise_on_validation_error=True,
        )
        self._locks: OrderedDict[str, anyio.Lock] = OrderedDict()

    def lock(self, refresh_token: str) -> anyio.Lock:
        """Serialises refreshes of one token within this process (bounded LRU; a held lock is never evicted)."""
        key = _key(refresh_token)
        if key in self._locks:
            self._locks.move_to_end(key)
            return self._locks[key]
        lock = self._locks[key] = anyio.Lock()
        if len(self._locks) > LOCK_CACHE_SIZE:
            idle = next((k for k, held in self._locks.items() if not held.locked() and k != key), None)
            if idle is not None:
                del self._locks[idle]
        return lock

    async def get(self, refresh_token: str, *, settle_seconds: float = 0) -> RotatedRefreshToken | None:
        """The pair this refresh token was rotated into, if within the window.

        ``settle_seconds`` waits once before a second look, for a rotation that
        another replica may be finishing right now.
        """
        rotated = await self._get(refresh_token)
        if rotated is None and settle_seconds:
            await anyio.sleep(settle_seconds)
            rotated = await self._get(refresh_token)
        return rotated

    async def _get(self, refresh_token: str) -> RotatedRefreshToken | None:
        rotated = await self._store.get(key=_key(refresh_token))
        if rotated is None or time.time() > rotated.grace_ends_at:
            return None
        return rotated

    async def record(self, refresh_token: str, client_id: str, tokens: OAuthToken) -> None:
        rotated = RotatedRefreshToken(client_id=client_id, tokens=tokens, rotated_at=time.time())
        await self._store.put(key=_key(refresh_token), value=rotated, ttl=GRACE_WINDOW_SECONDS)


def _key(refresh_token: str) -> str:
    """The old refresh token is stored only as a hash; the new pair itself is stored like FastMCP's own tokens."""
    return hashlib.sha256(refresh_token.encode()).hexdigest()
