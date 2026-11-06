"""OAuth token storage backends.

Selects and verifies the storage backing the OAuth token cache used by the
HTTP / SSE transports. Priority order (highest first):

0. ``REDIS_SENTINELS`` (non-empty) → Redis behind a customer-run Sentinel.
   ``REDIS_SENTINEL_MASTER`` names the master and is required. The client asks
   the sentinels for the current primary on every (re)connect, so it follows a
   failover. Wins over everything below; ``REDIS_URL`` and
   ``ELASTICACHE_SECRET_ARN`` are ignored with a warning.
1. ``REDIS_URL`` → Redis from a single connection URL. The URL carries host,
   port, db, credentials and TLS (``rediss://``) in one value; ``REDIS_SSL``
   is ignored here because the scheme already decides. ``REDIS_PASSWORD``
   applies only when the URL itself carries no password.
2. ``REDIS_PASSWORD`` + ``REDIS_HOST``/``REDIS_PORT`` → Redis with a static
   password (and ``REDIS_USERNAME`` for an ACL user, when set).
3. ``ELASTICACHE_SECRET_ARN`` + IRSA (``AWS_ROLE_ARN``) or EKS Pod Identity
   (``AWS_CONTAINER_CREDENTIALS_FULL_URI``) + host/port → Redis with a rotating
   AUTH token from AWS Secrets Manager.
4. ``REDIS_HOST`` + ``REDIS_PORT`` → plain Redis (no auth).
5. None of the above → in-memory store (dev only; tokens lost on restart).

On the host/port paths (2–4) ``REDIS_SSL`` turns on TLS for both the startup
PING and the token store's own connection. It defaults off, except on the
ElastiCache path (3), where it defaults on.

Sentinel mode (0) reads its own set of variables:

- ``REDIS_SENTINELS`` — ``host1:26379,host2:26379``; the port defaults to 26379.
- ``REDIS_SENTINEL_USERNAME`` / ``REDIS_SENTINEL_PASSWORD`` — auth to the sentinels.
- ``REDIS_USERNAME`` / ``REDIS_PASSWORD`` — auth to the data nodes.
- ``REDIS_DB`` — database index, default 0.
- ``REDIS_SSL`` — TLS to the data nodes, default off.
- ``REDIS_SENTINEL_SSL`` — TLS to the sentinels; unset or empty follows ``REDIS_SSL``.
- ``REDIS_SSL_VERIFY`` — verify server certificates against the system CA
  bundle (both hops), default off.

Misconfigurations raise ``RuntimeError`` at startup. Reachability is verified
eagerly with a synchronous PING.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastmcp.utilities.logging import get_logger
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore

logger = get_logger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

# Sentinel's well-known port, used when a REDIS_SENTINELS entry carries none.
_SENTINEL_DEFAULT_PORT = 26379

# Socket timeouts in Sentinel mode. A primary that vanishes mid-failover must
# surface as a timeout (which the client retries against the newly discovered
# primary) rather than a read that waits on the dead socket indefinitely.
_SENTINEL_SOCKET_TIMEOUT_SECONDS = 5.0

# Both build_token_store() calls in HTTP mode (OAuth + SSE) resolve the same
# environment; warn about the ignored variables once, not per call.
_sentinel_override_warned = False


def _has_aws_credentials() -> bool:
    """True when IRSA or EKS Pod Identity env vars are set."""
    return bool(os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI"))


def _redis_ssl_enabled(default: bool) -> bool:
    """Read REDIS_SSL with the caller's default. ElastiCache paths default True."""
    raw = os.getenv("REDIS_SSL")
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _env_str(name: str) -> str | None:
    """The variable's value, or None when unset or blank.

    The Helm chart renders every key it knows, so an unused setting arrives as
    an empty string and has to mean the same as an absent one.
    """
    value = (os.getenv(name) or "").strip()
    return value or None


def _env_secret(name: str) -> str | None:
    """A credential's value, or None when unset or empty. Not stripped: the
    whitespace may be part of the password."""
    return os.getenv(name) or None


def _env_flag(name: str, default: bool) -> bool:
    """A boolean variable; unset or blank gives ``default``."""
    value = _env_str(name)
    if value is None:
        return default
    return value.lower() in _TRUTHY


def _redis_client(
    host: str,
    port: int,
    *,
    username: str | None = None,
    password: str | None = None,
    ssl: bool = False,
) -> Any:
    """The async client a host/port token store connects with.

    Built here rather than by ``RedisStore(host=..., password=...)``: that
    constructor takes no TLS setting in the locked py-key-value-aio, so the
    store connected in plaintext — sending ``REDIS_PASSWORD`` in the clear —
    even when ``REDIS_SSL`` had made the startup PING use TLS.
    """
    from redis.asyncio import Redis as AsyncRedis

    return AsyncRedis(host=host, port=port, username=username, password=password, decode_responses=True, ssl=ssl)


def _redact_url(url: str) -> str:
    """The URL with any password replaced, safe to log."""
    parsed = urlparse(url)
    if not parsed.password:
        return url
    netloc = f"{parsed.username or ''}:***@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=netloc))


def _url_password_override(url: str, password: str | None) -> dict[str, str]:
    """Extra client kwargs for a URL-configured connection.

    The URL's own credentials win; ``REDIS_PASSWORD`` fills in only when the
    URL carries none. Returned as kwargs rather than a value so an absent
    override cannot clobber a URL-embedded password with ``None``.
    """
    if password and not urlparse(url).password:
        return {"password": password}
    return {}


def _ping_redis_url(url: str, *, password: str | None = None, timeout_seconds: float = 5.0) -> None:
    """Verify Redis reachability from a connection URL with a one-shot PING.

    ``redis.Redis.from_url`` honours the scheme (``rediss://`` enables TLS)
    and any query parameters. Raises ``RuntimeError`` on failure.
    """
    import redis  # local: only loaded when Redis is configured

    client = redis.Redis.from_url(
        url,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
        **_url_password_override(url, password),
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


def _ping_redis(
    host: str,
    port: int,
    *,
    username: str | None = None,
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
        username=username,
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


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SentinelConfig:
    """Sentinel-mode settings resolved from the environment.

    ``repr`` leaves the credentials out, so the object is safe to log.
    """

    sentinels: list[tuple[str, int]]
    master: str
    db: int
    ssl: bool
    sentinel_ssl: bool
    ssl_verify: bool
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    sentinel_username: str | None = field(default=None, repr=False)
    sentinel_password: str | None = field(default=None, repr=False)

    def describe_sentinels(self) -> str:
        return ",".join(f"{host}:{port}" for host, port in self.sentinels)


def _parse_sentinels(raw: str) -> list[tuple[str, int]]:
    """``host1:26379,host2,[::1]:26380`` → ``[(host, port), ...]``.

    A missing port means 26379. IPv6 literals go in brackets.
    """
    sentinels: list[tuple[str, int]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        # Errors name the position, never the entry: a pasted URL or user:password@host would put a
        # secret in the logs.
        position = len(sentinels) + 1
        if "/" in entry or "@" in entry:
            raise RuntimeError(f"REDIS_SENTINELS entry {position} must be host:port.")
        if entry.startswith("["):
            host, sep, rest = entry[1:].partition("]")
            if not sep or (rest and not rest.startswith(":")):
                raise RuntimeError(f"REDIS_SENTINELS entry {position} is not [ipv6]:port.")
            port_text = rest[1:]
        elif entry.count(":") == 1:
            host, port_text = entry.split(":")
        elif ":" in entry:
            raise RuntimeError(f"REDIS_SENTINELS entry {position}: put an IPv6 address in brackets, [addr]:port.")
        else:
            host, port_text = entry, ""
        if not host:
            raise RuntimeError(f"REDIS_SENTINELS entry {position} has no host.")
        try:
            port = int(port_text) if port_text else _SENTINEL_DEFAULT_PORT
        except ValueError:
            raise RuntimeError(f"REDIS_SENTINELS entry {position} has a non-numeric port.") from None
        if not 0 < port < 65536:
            raise RuntimeError(f"REDIS_SENTINELS entry {position} has an out-of-range port.")
        sentinels.append((host, port))
    if not sentinels:
        raise RuntimeError("REDIS_SENTINELS is set but lists no sentinel addresses.")
    return sentinels


def _sentinel_config(raw_sentinels: str) -> _SentinelConfig:
    """Resolve and validate Sentinel mode's variables. Raises ``RuntimeError``."""
    master = _env_str("REDIS_SENTINEL_MASTER")
    if not master:
        raise RuntimeError(
            "REDIS_SENTINELS is set but REDIS_SENTINEL_MASTER is not — set it to the master name "
            "your sentinels monitor (the name in `sentinel monitor <name> ...`)."
        )

    db_text = _env_str("REDIS_DB") or "0"
    try:
        db = int(db_text)
    except ValueError:
        raise RuntimeError(f"REDIS_DB must be a non-negative integer, got {db_text!r}.") from None
    if db < 0:
        raise RuntimeError(f"REDIS_DB must be a non-negative integer, got {db_text!r}.")

    ssl = _redis_ssl_enabled(default=False)
    return _SentinelConfig(
        sentinels=_parse_sentinels(raw_sentinels),
        master=master,
        db=db,
        ssl=ssl,
        sentinel_ssl=_env_flag("REDIS_SENTINEL_SSL", default=ssl),
        ssl_verify=_env_flag("REDIS_SSL_VERIFY", default=False),
        username=_env_str("REDIS_USERNAME"),
        password=_env_secret("REDIS_PASSWORD"),
        sentinel_username=_env_str("REDIS_SENTINEL_USERNAME"),
        sentinel_password=_env_secret("REDIS_SENTINEL_PASSWORD"),
    )


def _tls_kwargs(enabled: bool, verify: bool) -> dict[str, Any]:
    """redis-py TLS kwargs for one hop (data nodes or sentinels).

    ``REDIS_SSL_VERIFY`` off (the default, shared with Plane's other services)
    encrypts without checking the certificate: sentinels usually announce the
    primary by IP, which few certificates name. On, the certificate chain and
    hostname are checked against the system CA bundle (redis-py's
    ``ssl.create_default_context()``; ``SSL_CERT_FILE`` points it elsewhere).

    This applies to Sentinel mode only. The host/port paths keep redis-py's
    own default, which verifies.
    """
    if not enabled:
        return {}
    return {
        "ssl": True,
        "ssl_cert_reqs": "required" if verify else "none",
        "ssl_check_hostname": verify,
    }


def _sentinel_node_kwargs(cfg: _SentinelConfig, timeout_seconds: float) -> dict[str, Any]:
    """Connection kwargs for the sentinels themselves."""
    return {
        "username": cfg.sentinel_username,
        "password": cfg.sentinel_password,
        "socket_connect_timeout": timeout_seconds,
        "socket_timeout": timeout_seconds,
        **_tls_kwargs(cfg.sentinel_ssl, cfg.ssl_verify),
    }


def _data_node_kwargs(cfg: _SentinelConfig, timeout_seconds: float) -> dict[str, Any]:
    """Connection kwargs for the primary that ``master_for`` resolves."""
    return {
        "db": cfg.db,
        "username": cfg.username,
        "password": cfg.password,
        "socket_connect_timeout": timeout_seconds,
        "socket_timeout": timeout_seconds,
        **_tls_kwargs(cfg.ssl, cfg.ssl_verify),
    }


def _sentinel_diagnosis(sentinel_clients: list[Any]) -> str:
    """One line per sentinel saying why it gave no master.

    Replaces redis-py's ``MasterNotFoundError`` text, which embeds the full
    repr of every sentinel client. Each sentinel is pinged on its own:
    an error here is a reachability, auth or TLS problem; a reply means it is
    reachable but has no healthy master by that name.
    """
    parts = []
    for client in sentinel_clients:
        kwargs = client.connection_pool.connection_kwargs
        addr = f"{kwargs.get('host')}:{kwargs.get('port')}"
        try:
            client.ping()
            parts.append(f"{addr} reachable, reports no usable master by that name")
        except Exception as exc:
            parts.append(f"{addr} {type(exc).__name__}: {exc}")
    return "; ".join(parts)


def _ping_redis_sentinel(cfg: _SentinelConfig, *, timeout_seconds: float = 5.0) -> None:
    """Resolve the master through the sentinels and PING it, synchronously.

    Raises ``RuntimeError`` naming which step failed: no sentinel could name
    the master, or the master it named did not answer.
    """
    from redis.sentinel import MasterNotFoundError, Sentinel

    sentinel = Sentinel(
        cfg.sentinels,
        sentinel_kwargs=_sentinel_node_kwargs(cfg, timeout_seconds),
        **_data_node_kwargs(cfg, timeout_seconds),
    )
    client = sentinel.master_for(cfg.master)
    try:
        client.ping()
    except MasterNotFoundError as exc:
        raise RuntimeError(
            f"Redis Sentinel: no sentinel in REDIS_SENTINELS returned a usable master named {cfg.master!r}. "
            f"Check the address list, REDIS_SENTINEL_MASTER, sentinel auth (REDIS_SENTINEL_USERNAME/PASSWORD) "
            f"and REDIS_SENTINEL_SSL. Per sentinel: {_sentinel_diagnosis(sentinel.sentinels)}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Redis Sentinel: master {cfg.master!r} was resolved but the startup PING failed. Check data-node "
            f"auth (REDIS_USERNAME/PASSWORD), REDIS_DB, REDIS_SSL and REDIS_SSL_VERIFY. Detail: {exc}"
        ) from exc
    finally:
        for c in (client, *sentinel.sentinels):
            try:
                c.close()
            except Exception:
                pass

    logger.info("Redis connection verified via Sentinel (PING succeeded)")


def _sentinel_client(cfg: _SentinelConfig) -> Any:
    """The async client the Sentinel token store connects with.

    ``master_for`` returns a ``redis.asyncio.Redis`` over a
    ``SentinelConnectionPool``: every new connection asks the sentinels for the
    current primary, and a ``READONLY`` reply from a demoted primary drops the
    connection so the retry lands on the new one. The pool is built from the
    caller's kwargs, not the ``Redis()`` defaults, so the retry policy has to
    be passed explicitly — without it a failover surfaces as an error on the
    first request that hits it.
    """
    from redis.asyncio.retry import Retry
    from redis.asyncio.sentinel import Sentinel
    from redis.backoff import ExponentialWithJitterBackoff

    sentinel = Sentinel(
        cfg.sentinels,
        sentinel_kwargs=_sentinel_node_kwargs(cfg, _SENTINEL_SOCKET_TIMEOUT_SECONDS),
        **_data_node_kwargs(cfg, _SENTINEL_SOCKET_TIMEOUT_SECONDS),
    )
    return sentinel.master_for(
        cfg.master,
        decode_responses=True,
        retry=Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3),
    )


def _warn_sentinel_overrides(*, redis_url: str | None, secret_arn: str | None) -> None:
    """Log once that Sentinel mode is ignoring a URL / ElastiCache setting."""
    global _sentinel_override_warned
    ignored = [name for name, value in (("REDIS_URL", redis_url), ("ELASTICACHE_SECRET_ARN", secret_arn)) if value]
    if not ignored or _sentinel_override_warned:
        return
    _sentinel_override_warned = True
    logger.warning("REDIS_SENTINELS is set — Sentinel mode wins, %s ignored.", " and ".join(ignored))


def _auth_kind(username: str | None, password: str | None) -> str:
    """How a hop authenticates, for logging without the credentials."""
    if username and password:
        return "acl"
    return "password" if password else "none"


def build_token_store() -> Any:
    """Select and build the OAuth token store from environment variables.

    See the module docstring for the selection priority. The returned object
    is handed verbatim to ``PlaneOAuthProvider`` as ``client_storage``.
    """
    redis_url = os.getenv("REDIS_URL")
    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")
    password = os.getenv("REDIS_PASSWORD")
    secret_arn = os.getenv("ELASTICACHE_SECRET_ARN")

    # 0. Redis behind a customer-run Sentinel
    raw_sentinels = _env_str("REDIS_SENTINELS")
    if raw_sentinels:
        cfg = _sentinel_config(raw_sentinels)
        _warn_sentinel_overrides(redis_url=redis_url, secret_arn=secret_arn)

        _ping_redis_sentinel(cfg)
        store = RedisStore(client=_sentinel_client(cfg))
        logger.info(
            "Token store: Redis Sentinel (master=%s, sentinels=%s, db=%s, ssl=%s, sentinel_ssl=%s, "
            "ssl_verify=%s, auth=%s, sentinel_auth=%s)",
            cfg.master,
            cfg.describe_sentinels(),
            cfg.db,
            cfg.ssl,
            cfg.sentinel_ssl,
            cfg.ssl_verify,
            _auth_kind(cfg.username, cfg.password),
            _auth_kind(cfg.sentinel_username, cfg.sentinel_password),
        )
        return store

    # 1. Redis by connection URL
    if redis_url:
        if secret_arn:
            logger.warning("Both REDIS_URL and ELASTICACHE_SECRET_ARN set — the URL wins, Secrets Manager ignored.")
        if redis_host or redis_port:
            logger.warning("Both REDIS_URL and REDIS_HOST/REDIS_PORT set — the URL wins, host/port ignored.")

        _ping_redis_url(redis_url, password=password)

        # Not RedisStore(url=...): its own URL parser keeps host/port/db but
        # drops the scheme, silently downgrading rediss:// to plaintext.
        # redis-py's from_url honours the scheme and query parameters.
        from redis.asyncio import Redis as AsyncRedis

        async_client = AsyncRedis.from_url(
            redis_url,
            decode_responses=True,
            **_url_password_override(redis_url, password),
        )
        store = RedisStore(client=async_client)
        logger.info("Token store: Redis (url=%s)", _redact_url(redis_url))
        return store

    # 2. Static-password Redis
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
        # REDIS_USERNAME selects an ACL user; unset keeps the legacy AUTH <password>.
        username = _env_str("REDIS_USERNAME")
        _ping_redis(redis_host, int(redis_port), username=username, password=password, ssl=use_ssl)
        store = RedisStore(
            client=_redis_client(redis_host, int(redis_port), username=username, password=password, ssl=use_ssl)
        )
        logger.info(
            "Token store: Redis (auth=%s, host=%s, port=%s, ssl=%s)",
            _auth_kind(username, password),
            redis_host,
            redis_port,
            use_ssl,
        )
        return store

    # 3. ElastiCache + Secrets Manager
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

    # 4. Plain Redis
    if redis_host and redis_port:
        use_ssl = _redis_ssl_enabled(default=False)
        _ping_redis(redis_host, int(redis_port), ssl=use_ssl)
        store = RedisStore(client=_redis_client(redis_host, int(redis_port), ssl=use_ssl))
        logger.info("Token store: Redis (auth=none, host=%s, port=%s, ssl=%s)", redis_host, redis_port, use_ssl)
        return store

    # 5. In-memory fallback
    logger.warning(
        "Token store: in-memory (tokens lost on restart). Set REDIS_URL or REDIS_HOST and REDIS_PORT for production."
    )
    return MemoryStore()
