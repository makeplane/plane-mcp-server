"""OAuth token storage backends.

Selects and verifies the storage backing the OAuth token cache used by the
HTTP / SSE transports. Priority order (highest first):

1. ``REDIS_PASSWORD`` + ``REDIS_HOST``/``REDIS_PORT`` → Redis with a static password.
2. ``ELASTICACHE_SECRET_ARN`` + IRSA (``AWS_ROLE_ARN``) or EKS Pod Identity
   (``AWS_CONTAINER_CREDENTIALS_FULL_URI``) + host/port → Redis with a rotating
   AUTH token from AWS Secrets Manager.
3. ``REDIS_HOST`` + ``REDIS_PORT`` → plain Redis (no auth).
4. None of the above → in-memory store (dev only; tokens lost on restart).

Misconfigurations raise ``RuntimeError`` at startup. Reachability is verified
eagerly with a synchronous PING.
"""

from __future__ import annotations

import os
from typing import Any

from fastmcp.utilities.logging import get_logger
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore

logger = get_logger(__name__)


def _has_aws_credentials() -> bool:
    """True when IRSA or EKS Pod Identity env vars are set."""
    return bool(os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI"))


def _redis_ssl_enabled(default: bool) -> bool:
    """Read REDIS_SSL with the caller's default. ElastiCache paths default True."""
    raw = os.getenv("REDIS_SSL")
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _ping_redis(
    host: str,
    port: int,
    *,
    password: str | None = None,
    ssl: bool = False,
    timeout_seconds: float = 5.0,
) -> None:
    """Verify Redis reachability with a one-shot synchronous PING.

    Raises ``RuntimeError`` on any connection or auth failure.
    """
    import redis  # local: only loaded when Redis is configured

    client = redis.Redis(
        host=host,
        port=port,
        password=password,
        ssl=ssl,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        client.ping()
    except Exception as exc:
        raise RuntimeError(f"Redis connection failed during startup PING: {exc}") from exc
    finally:
        try:
            client.close()
        except Exception:
            pass

    logger.info("Redis connection verified (PING succeeded)")


def build_token_store() -> Any:
    """Select and build the OAuth token store from environment variables.

    See the module docstring for the selection priority. The returned object
    is handed verbatim to ``PlaneOAuthProvider`` as ``client_storage``.
    """
    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")
    password = os.getenv("REDIS_PASSWORD")
    secret_arn = os.getenv("ELASTICACHE_SECRET_ARN")

    # 1. Static-password Redis
    if password:
        if not (redis_host and redis_port):
            raise RuntimeError("REDIS_PASSWORD is set but REDIS_HOST/REDIS_PORT are not — set both to use Redis.")
        if secret_arn:
            logger.warning(
                "Both REDIS_PASSWORD and ELASTICACHE_SECRET_ARN set — static password wins, Secrets Manager ignored."
            )

        # REDIS_SSL defaults False here: plain Redis is the common case for
        # static-password deployments. Set REDIS_SSL=true for TLS-fronted Redis.
        use_ssl = _redis_ssl_enabled(default=False)
        _ping_redis(redis_host, int(redis_port), password=password, ssl=use_ssl)
        store = RedisStore(host=redis_host, port=int(redis_port), password=password)
        logger.info(
            "Token store: Redis (auth=password, host=%s, port=%s, ssl=%s)",
            redis_host,
            redis_port,
            use_ssl,
        )
        return store

    # 2. ElastiCache + Secrets Manager
    if secret_arn and _has_aws_credentials():
        if not (redis_host and redis_port):
            raise RuntimeError(
                "ELASTICACHE_SECRET_ARN is set but REDIS_HOST/REDIS_PORT are not — set both to use Redis."
            )

        # Lazy imports keep the AWS path's deps optional.
        from redis.asyncio import Redis as AsyncRedis

        from plane_mcp.aws_secrets import ElastiCacheCredentialProvider, get_secret

        region = os.getenv("AWS_REGION", "us-east-1")
        token_key = os.getenv("REDIS_AUTH_TOKEN_KEY", "authToken")

        # Eager fetch — a missing key fails startup, not the first OAuth flow.
        secret = get_secret(secret_arn, region)
        if token_key not in secret:
            raise RuntimeError(f"REDIS_AUTH_TOKEN_KEY {token_key!r} not present in secret {secret_arn}")

        use_ssl = _redis_ssl_enabled(default=True)
        _ping_redis(redis_host, int(redis_port), password=secret[token_key], ssl=use_ssl)

        async_client = AsyncRedis(
            host=redis_host,
            port=int(redis_port),
            credential_provider=ElastiCacheCredentialProvider(secret_arn, region, token_key),
            decode_responses=True,
            ssl=use_ssl,
        )
        store = RedisStore(client=async_client)
        logger.info(
            "Token store: Redis (auth=secrets-manager, host=%s, port=%s, ssl=%s, region=%s)",
            redis_host,
            redis_port,
            use_ssl,
            region,
        )
        return store

    if secret_arn:
        logger.warning(
            "ELASTICACHE_SECRET_ARN is set but IRSA / Pod Identity env vars "
            "(AWS_ROLE_ARN or AWS_CONTAINER_CREDENTIALS_FULL_URI) are missing — "
            "skipping Secrets Manager auth."
        )

    # 3. Plain Redis
    if redis_host and redis_port:
        _ping_redis(redis_host, int(redis_port))
        store = RedisStore(host=redis_host, port=int(redis_port))
        logger.info("Token store: Redis (auth=none, host=%s, port=%s)", redis_host, redis_port)
        return store

    # 4. In-memory fallback
    logger.warning("Token store: in-memory (tokens lost on restart). Set REDIS_HOST and REDIS_PORT for production.")
    return MemoryStore()
