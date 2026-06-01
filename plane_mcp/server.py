"""Plane MCP Server implementation."""

import os

from fastmcp import FastMCP
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.utilities.logging import get_logger
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore
from mcp.types import Icon

from plane_mcp.auth import PlaneHeaderAuthProvider, PlaneOAuthProvider
from plane_mcp.tools import register_tools

logger = get_logger(__name__)


def _has_aws_credentials() -> bool:
    # True when either IRSA (AWS_ROLE_ARN) or EKS Pod Identity
    # (AWS_CONTAINER_CREDENTIALS_FULL_URI) is present. Mirrors plane-ee api service.
    return bool(os.environ.get("AWS_ROLE_ARN", "") or os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI", ""))


def _redis_ssl_enabled() -> bool:
    # ElastiCache requires in-transit encryption (TLS) whenever Redis AUTH tokens
    # are used, so default to True. Set REDIS_SSL=false to opt out for a plaintext
    # self-managed Redis.
    return os.getenv("REDIS_SSL", "true").strip().lower() in ("1", "true", "yes", "on")


def _verify_redis_connection(store) -> None:
    """PING Redis eagerly so a misconfigured/unreachable backend fails loud at
    startup instead of silently degrading on the first (lazy) token operation."""
    import asyncio

    try:
        asyncio.run(store._client.ping())
    except Exception as exc:
        raise RuntimeError(f"Redis connection failed during startup PING: {exc}") from exc
    logger.info("Redis connection verified (PING succeeded)")


def _build_token_store():
    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")
    password = os.getenv("REDIS_PASSWORD")
    secret_arn = os.getenv("ELASTICACHE_SECRET_ARN")

    if password:
        if not (redis_host and redis_port):
            raise RuntimeError("REDIS_PASSWORD is set but REDIS_HOST/REDIS_PORT are not — set both to use Redis.")
        logger.info("Using Redis for token storage (password auth)")
        store = RedisStore(host=redis_host, port=int(redis_port), password=password)
        _verify_redis_connection(store)
        return store

    if secret_arn and _has_aws_credentials():
        if not (redis_host and redis_port):
            raise RuntimeError(
                "ELASTICACHE_SECRET_ARN is set but REDIS_HOST/REDIS_PORT are not — set both to use Redis."
            )

        from redis.asyncio import Redis

        from plane_mcp.aws_secrets import ElastiCacheCredentialProvider, get_secret

        region = os.getenv("AWS_REGION", "us-east-1")
        token_key = os.getenv("REDIS_AUTH_TOKEN_KEY", "authToken")

        secret = get_secret(secret_arn, region)
        if token_key not in secret:
            raise RuntimeError(f"REDIS_AUTH_TOKEN_KEY {token_key!r} not present in secret {secret_arn}")

        use_ssl = _redis_ssl_enabled()
        logger.info("Using Redis for token storage (auth token from Secrets Manager, ssl=%s)", use_ssl)
        client = Redis(
            host=redis_host,
            port=int(redis_port),
            credential_provider=ElastiCacheCredentialProvider(secret_arn, region, token_key),
            decode_responses=True,
            ssl=use_ssl,
        )
        store = RedisStore(client=client)
        _verify_redis_connection(store)
        return store

    if secret_arn:
        logger.warning(
            "ELASTICACHE_SECRET_ARN is set but IRSA/Pod Identity env vars "
            "(AWS_ROLE_ARN or AWS_CONTAINER_CREDENTIALS_FULL_URI) are missing — skipping Secrets Manager auth."
        )

    if redis_host and redis_port:
        logger.info("Using Redis for token storage")
        store = RedisStore(host=redis_host, port=int(redis_port))
        _verify_redis_connection(store)
        return store

    logger.warning(
        "Using in-memory storage - tokens will be lost on restart! Set REDIS_HOST and REDIS_PORT for production."
    )
    return MemoryStore()


def get_oauth_mcp(base_path: str = "/"):
    client_storage = _build_token_store()

    # Initialize the MCP server
    oauth_mcp = FastMCP(
        "Plane MCP Server",
        icons=[Icon(src="https://plane.so/favicon.ico", alt="Plane MCP Server")],
        website_url="https://plane.so",
        auth=PlaneOAuthProvider(
            client_id=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID", ""),
            client_secret=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET", ""),
            base_url=f"{os.getenv('PLANE_OAUTH_PROVIDER_BASE_URL')}{base_path}",
            plane_base_url=os.getenv("PLANE_BASE_URL", ""),
            plane_internal_base_url=os.getenv("PLANE_INTERNAL_BASE_URL", ""),
            client_storage=client_storage,
            required_scopes=["read", "write"],
            allowed_client_redirect_uris=[
                # Localhost only for http (dynamic ports from MCP clients)
                "http://localhost:*",
                "http://localhost:*/*",
                "http://127.0.0.1:*",
                "http://127.0.0.1:*/*",
                # Known MCP client custom protocol schemes
                "cursor://*",
                "vscode://*",
                "vscode-insiders://*",
                "windsurf://*",
                "claude://*",
            ],
        ),
    )
    oauth_mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=True))
    register_tools(oauth_mcp)
    return oauth_mcp


def get_header_mcp():
    header_mcp = FastMCP(
        "Plane MCP Server (header-http)",
        auth=PlaneHeaderAuthProvider(
            required_scopes=["read", "write"],
        ),
    )
    header_mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=True))
    register_tools(header_mcp)
    return header_mcp


def get_stdio_mcp():
    stdio_mcp = FastMCP(
        "Plane MCP Server (stdio)",
    )
    stdio_mcp.add_middleware(StructuredLoggingMiddleware(include_payloads=True))
    register_tools(stdio_mcp)
    return stdio_mcp
