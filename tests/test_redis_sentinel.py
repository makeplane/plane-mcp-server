"""Unit tests for the Redis Sentinel branch of the token-store resolver.

No Redis and no sentinels are needed: the synchronous startup PING is patched
out (or replaced with a scripted fake), and the async client is only built,
never connected — redis-py resolves the master lazily on first use.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore
from redis.asyncio.sentinel import SentinelConnectionPool

from plane_mcp import storage
from plane_mcp.storage import build_token_store

_REDIS_ENV = (
    "REDIS_SENTINELS",
    "REDIS_SENTINEL_MASTER",
    "REDIS_SENTINEL_USERNAME",
    "REDIS_SENTINEL_PASSWORD",
    "REDIS_SENTINEL_SSL",
    "REDIS_SSL_VERIFY",
    "REDIS_USERNAME",
    "REDIS_PASSWORD",
    "REDIS_DB",
    "REDIS_SSL",
    "REDIS_URL",
    "REDIS_HOST",
    "REDIS_PORT",
    "ELASTICACHE_SECRET_ARN",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    for k in _REDIS_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(storage, "_sentinel_override_warned", False)


@pytest.fixture
def no_ping(monkeypatch):
    """Skip every synchronous startup PING."""
    monkeypatch.setattr(storage, "_ping_redis", lambda *a, **kw: None)
    monkeypatch.setattr(storage, "_ping_redis_url", lambda *a, **kw: None)
    monkeypatch.setattr(storage, "_ping_redis_sentinel", lambda *a, **kw: None)


@pytest.fixture
def sentinel_env(monkeypatch):
    monkeypatch.setenv("REDIS_SENTINELS", "s1:26379,s2:26380,s3")
    monkeypatch.setenv("REDIS_SENTINEL_MASTER", "mymaster")


@pytest.fixture
def storage_log(caplog):
    """Every record from the storage logger (``fastmcp`` does not propagate)."""
    target = logging.getLogger("fastmcp.plane_mcp.storage")
    target.addHandler(caplog.handler)
    previous = target.level
    target.setLevel(logging.DEBUG)
    yield caplog
    target.removeHandler(caplog.handler)
    target.setLevel(previous)


def _pool(store: RedisStore) -> SentinelConnectionPool:
    pool = store._client.connection_pool
    assert isinstance(pool, SentinelConnectionPool)
    return pool


def _sentinel_kwargs(store: RedisStore) -> dict[str, Any]:
    """Connection kwargs of the first sentinel client behind the store."""
    return _pool(store).sentinel_manager.sentinels[0].connection_pool.connection_kwargs


# ---------------------------------------------------------------------------
# REDIS_SENTINELS parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("h1:26379", [("h1", 26379)]),
        ("h1", [("h1", 26379)]),
        (" h1:1 , h2 ,h3:3 ", [("h1", 1), ("h2", 26379), ("h3", 3)]),
        ("h1:26379,,", [("h1", 26379)]),
        ("[::1]:26380,[fe80::1]", [("::1", 26380), ("fe80::1", 26379)]),
        ("10.0.0.5:26379", [("10.0.0.5", 26379)]),
    ],
)
def test_parse_sentinels(raw, expected):
    assert storage._parse_sentinels(raw) == expected


@pytest.mark.parametrize("raw", ["h1:abc", ":26379", "h1:0", "h1:70000", "::1:26379", "[::1", " , "])
def test_parse_sentinels_rejects_bad_entries(raw):
    with pytest.raises(RuntimeError, match="REDIS_SENTINELS"):
        storage._parse_sentinels(raw)


# ---------------------------------------------------------------------------
# Selection and validation
# ---------------------------------------------------------------------------


def test_sentinel_mode_builds_a_master_for_client(monkeypatch, no_ping, sentinel_env):
    store = build_token_store()

    assert isinstance(store, RedisStore)
    pool = _pool(store)
    assert pool.service_name == "mymaster"
    assert pool.is_master is True
    hosts = [
        (s.connection_pool.connection_kwargs["host"], s.connection_pool.connection_kwargs["port"])
        for s in pool.sentinel_manager.sentinels
    ]
    assert hosts == [("s1", 26379), ("s2", 26380), ("s3", 26379)]
    kwargs = pool.connection_kwargs
    assert kwargs["db"] == 0
    assert kwargs["decode_responses"] is True
    assert pool.connection_class.__name__ == "SentinelManagedConnection"


def test_redis_store_keeps_the_client_it_is_given(monkeypatch, no_ping, sentinel_env):
    """RedisStore(client=...) must use the Sentinel client as-is, not rebuild one from a URL."""
    built: list[Any] = []
    original = storage._sentinel_client

    def _spy(cfg):
        client = original(cfg)
        built.append(client)
        return client

    monkeypatch.setattr(storage, "_sentinel_client", _spy)
    store = build_token_store()
    assert store._client is built[0]


def test_sentinel_mode_requires_master_name(monkeypatch, no_ping):
    monkeypatch.setenv("REDIS_SENTINELS", "s1:26379")
    with pytest.raises(RuntimeError, match="REDIS_SENTINEL_MASTER"):
        build_token_store()


def test_blank_master_name_counts_as_missing(monkeypatch, no_ping):
    monkeypatch.setenv("REDIS_SENTINELS", "s1:26379")
    monkeypatch.setenv("REDIS_SENTINEL_MASTER", "  ")
    with pytest.raises(RuntimeError, match="REDIS_SENTINEL_MASTER"):
        build_token_store()


def test_missing_master_fails_before_any_connection(monkeypatch):
    def _explode(*_a, **_kw):
        raise AssertionError("no connection may be attempted before the config is validated")

    monkeypatch.setattr(storage, "_ping_redis_sentinel", _explode)
    monkeypatch.setenv("REDIS_SENTINELS", "s1:26379")
    with pytest.raises(RuntimeError, match="REDIS_SENTINEL_MASTER"):
        build_token_store()


def test_empty_sentinels_keeps_todays_behaviour(monkeypatch, no_ping):
    """The chart renders REDIS_SENTINELS='' when Sentinel is off."""
    monkeypatch.setenv("REDIS_SENTINELS", "")
    assert isinstance(build_token_store(), MemoryStore)

    monkeypatch.setenv("REDIS_URL", "redis://url-host:6390/2")
    store = build_token_store()
    assert not isinstance(store._client.connection_pool, SentinelConnectionPool)
    assert store._client.connection_pool.connection_kwargs["host"] == "url-host"


@pytest.mark.parametrize("value", ["x", "-1", "1.5"])
def test_invalid_db_raises(monkeypatch, no_ping, sentinel_env, value):
    monkeypatch.setenv("REDIS_DB", value)
    with pytest.raises(RuntimeError, match="REDIS_DB"):
        build_token_store()


def test_db_and_data_node_credentials(monkeypatch, no_ping, sentinel_env):
    monkeypatch.setenv("REDIS_DB", "3")
    monkeypatch.setenv("REDIS_USERNAME", "plane")
    monkeypatch.setenv("REDIS_PASSWORD", "data-pw")
    kwargs = _pool(build_token_store()).connection_kwargs
    assert kwargs["db"] == 3
    assert kwargs["username"] == "plane"
    assert kwargs["password"] == "data-pw"


def test_sentinel_credentials_stay_on_the_sentinels(monkeypatch, no_ping, sentinel_env):
    monkeypatch.setenv("REDIS_PASSWORD", "data-pw")
    monkeypatch.setenv("REDIS_SENTINEL_USERNAME", "sentinel-user")
    monkeypatch.setenv("REDIS_SENTINEL_PASSWORD", "sentinel-pw")
    store = build_token_store()

    sentinel = _sentinel_kwargs(store)
    assert sentinel["username"] == "sentinel-user"
    assert sentinel["password"] == "sentinel-pw"
    assert _pool(store).connection_kwargs["password"] == "data-pw"


def test_no_sentinel_auth_by_default(monkeypatch, no_ping, sentinel_env):
    """The data-node password is not sent to the sentinels unless asked for."""
    monkeypatch.setenv("REDIS_PASSWORD", "data-pw")
    sentinel = _sentinel_kwargs(build_token_store())
    assert sentinel["password"] is None
    assert sentinel["username"] is None


def test_store_client_retries_across_a_failover(monkeypatch, no_ping, sentinel_env):
    """A pool-backed client takes its retry policy from the pool, not Redis() defaults."""
    retry = _pool(build_token_store()).connection_kwargs["retry"]
    assert retry.get_retries() == 3


def test_sentinel_wins_over_url_and_warns_once(monkeypatch, no_ping, sentinel_env, storage_log):
    monkeypatch.setenv("REDIS_URL", "redis://url-host:6379/0")
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:x")

    # HTTP mode builds the store twice (OAuth + SSE).
    first = build_token_store()
    second = build_token_store()

    for store in (first, second):
        assert isinstance(store._client.connection_pool, SentinelConnectionPool)
    warnings = [r.getMessage() for r in storage_log.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "REDIS_URL" in warnings[0] and "ELASTICACHE_SECRET_ARN" in warnings[0]


def test_no_warning_without_overrides(monkeypatch, no_ping, sentinel_env, storage_log):
    build_token_store()
    assert not [r for r in storage_log.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


def test_tls_off_by_default(monkeypatch, no_ping, sentinel_env):
    store = build_token_store()
    assert _pool(store).connection_class.__name__ == "SentinelManagedConnection"
    assert _pool(store).sentinel_manager.sentinels[0].connection_pool.connection_class.__name__ == "Connection"


def test_redis_ssl_turns_on_tls_for_both_hops_without_verification(monkeypatch, no_ping, sentinel_env):
    monkeypatch.setenv("REDIS_SSL", "true")
    store = build_token_store()

    pool = _pool(store)
    assert pool.connection_class.__name__ == "SentinelManagedSSLConnection"
    assert pool.connection_kwargs["ssl_cert_reqs"] == "none"
    assert pool.connection_kwargs["ssl_check_hostname"] is False

    sentinel_pool = pool.sentinel_manager.sentinels[0].connection_pool
    assert sentinel_pool.connection_class.__name__ == "SSLConnection"
    assert sentinel_pool.connection_kwargs["ssl_cert_reqs"] == "none"


@pytest.mark.parametrize("sentinel_ssl", [None, ""])
def test_sentinel_ssl_unset_or_blank_follows_redis_ssl(monkeypatch, no_ping, sentinel_env, sentinel_ssl):
    monkeypatch.setenv("REDIS_SSL", "yes")
    if sentinel_ssl is not None:
        monkeypatch.setenv("REDIS_SENTINEL_SSL", sentinel_ssl)
    store = build_token_store()
    assert _pool(store).sentinel_manager.sentinels[0].connection_pool.connection_class.__name__ == "SSLConnection"


def test_sentinel_ssl_set_independently(monkeypatch, no_ping, sentinel_env):
    """TLS on the data nodes only, and on the sentinels only."""
    monkeypatch.setenv("REDIS_SSL", "true")
    monkeypatch.setenv("REDIS_SENTINEL_SSL", "false")
    store = build_token_store()
    assert _pool(store).connection_class.__name__ == "SentinelManagedSSLConnection"
    assert _pool(store).sentinel_manager.sentinels[0].connection_pool.connection_class.__name__ == "Connection"

    monkeypatch.setenv("REDIS_SSL", "false")
    monkeypatch.setenv("REDIS_SENTINEL_SSL", "ON")
    store = build_token_store()
    assert _pool(store).connection_class.__name__ == "SentinelManagedConnection"
    assert _pool(store).sentinel_manager.sentinels[0].connection_pool.connection_class.__name__ == "SSLConnection"


def test_ssl_verify_checks_certificate_and_hostname(monkeypatch, no_ping, sentinel_env):
    monkeypatch.setenv("REDIS_SSL", "true")
    monkeypatch.setenv("REDIS_SSL_VERIFY", "true")
    store = build_token_store()

    for kwargs in (_pool(store).connection_kwargs, _sentinel_kwargs(store)):
        assert kwargs["ssl_cert_reqs"] == "required"
        assert kwargs["ssl_check_hostname"] is True
        # The system CA bundle: no explicit CA file overrides redis-py's default context.
        assert kwargs.get("ssl_ca_certs") is None


# ---------------------------------------------------------------------------
# Startup PING
# ---------------------------------------------------------------------------


class _FakeSentinelNode:
    """One sentinel's sync client: answers PING or raises."""

    def __init__(self, host, port, exc):
        self.connection_pool = type("_Pool", (), {"connection_kwargs": {"host": host, "port": port}})()
        self._exc = exc

    def ping(self):
        if self._exc is not None:
            raise self._exc
        return True

    def close(self):
        pass


class _FakeSyncSentinel:
    """Stands in for ``redis.sentinel.Sentinel``; records how it was built."""

    calls: list[dict[str, Any]] = []
    ping_exc: Exception | None = None
    sentinel_errors: dict[str, Exception] = {}

    def __init__(self, sentinels, *, sentinel_kwargs, **connection_kwargs):
        type(self).calls.append(
            {"sentinels": sentinels, "sentinel_kwargs": sentinel_kwargs, "connection_kwargs": connection_kwargs}
        )
        self.sentinels = [
            _FakeSentinelNode(host, port, type(self).sentinel_errors.get(host)) for host, port in sentinels
        ]

    def master_for(self, name):
        exc = type(self).ping_exc

        class _Client:
            def ping(self):
                if exc is not None:
                    raise exc
                return True

            def close(self):
                pass

        return _Client()


@pytest.fixture
def fake_sync_sentinel(monkeypatch):
    import redis.sentinel

    _FakeSyncSentinel.calls = []
    _FakeSyncSentinel.ping_exc = None
    _FakeSyncSentinel.sentinel_errors = {}
    monkeypatch.setattr(redis.sentinel, "Sentinel", _FakeSyncSentinel)
    return _FakeSyncSentinel


def test_ping_uses_the_same_settings_as_the_store(monkeypatch, sentinel_env, fake_sync_sentinel):
    monkeypatch.setenv("REDIS_SSL", "true")
    monkeypatch.setenv("REDIS_DB", "2")
    monkeypatch.setenv("REDIS_PASSWORD", "data-pw")
    monkeypatch.setenv("REDIS_SENTINEL_PASSWORD", "sentinel-pw")

    build_token_store()

    call = fake_sync_sentinel.calls[0]
    assert call["sentinels"] == [("s1", 26379), ("s2", 26380), ("s3", 26379)]
    assert call["connection_kwargs"]["db"] == 2
    assert call["connection_kwargs"]["password"] == "data-pw"
    assert call["connection_kwargs"]["ssl"] is True
    assert call["sentinel_kwargs"]["password"] == "sentinel-pw"
    assert call["sentinel_kwargs"]["ssl"] is True
    assert call["sentinel_kwargs"]["socket_timeout"]


def test_ping_reports_unresolvable_master_per_sentinel(monkeypatch, sentinel_env, fake_sync_sentinel):
    from redis.exceptions import AuthenticationError
    from redis.sentinel import MasterNotFoundError

    fake_sync_sentinel.ping_exc = MasterNotFoundError("No master found for 'mymaster' : <huge client reprs>")
    fake_sync_sentinel.sentinel_errors = {"s1": AuthenticationError("invalid password"), "s3": OSError("refused")}
    with pytest.raises(RuntimeError) as info:
        build_token_store()

    message = str(info.value)
    assert "no sentinel in REDIS_SENTINELS returned a usable master named 'mymaster'" in message
    assert "s1:26379 AuthenticationError: invalid password" in message
    assert "s2:26380 reachable, reports no usable master by that name" in message
    assert "s3:26379 OSError: refused" in message
    assert "huge client reprs" not in message


def test_ping_reports_unreachable_master(monkeypatch, sentinel_env, fake_sync_sentinel):
    from redis.exceptions import AuthenticationError

    fake_sync_sentinel.ping_exc = AuthenticationError("invalid username-password pair")
    with pytest.raises(RuntimeError, match="master 'mymaster' was resolved but the startup PING failed"):
        build_token_store()


# ---------------------------------------------------------------------------
# Credentials never reach the logs
# ---------------------------------------------------------------------------


def test_credentials_are_not_logged(monkeypatch, sentinel_env, fake_sync_sentinel, storage_log):
    secrets = {
        "REDIS_USERNAME": "data-user-secret",
        "REDIS_PASSWORD": "data-pw-secret",
        "REDIS_SENTINEL_USERNAME": "sentinel-user-secret",
        "REDIS_SENTINEL_PASSWORD": "sentinel-pw-secret",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("REDIS_URL", "redis://:url-pw-secret@url-host:6379/0")

    build_token_store()

    logged = "\n".join(r.getMessage() for r in storage_log.records)
    assert "Redis Sentinel" in logged
    assert "auth=acl" in logged and "sentinel_auth=acl" in logged
    for value in (*secrets.values(), "url-pw-secret"):
        assert value not in logged


def test_config_repr_omits_credentials(monkeypatch, sentinel_env):
    monkeypatch.setenv("REDIS_PASSWORD", "data-pw-secret")
    monkeypatch.setenv("REDIS_SENTINEL_PASSWORD", "sentinel-pw-secret")
    text = repr(storage._sentinel_config("s1"))
    assert "data-pw-secret" not in text and "sentinel-pw-secret" not in text


def test_passwords_are_not_stripped(monkeypatch, no_ping, sentinel_env):
    monkeypatch.setenv("REDIS_PASSWORD", " pw with spaces ")
    assert _pool(build_token_store()).connection_kwargs["password"] == " pw with spaces "


# ---------------------------------------------------------------------------
# REDIS_USERNAME on the non-Sentinel host/port password path
# ---------------------------------------------------------------------------


def test_password_path_passes_redis_username(monkeypatch, no_ping):
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("REDIS_USERNAME", "plane")
    kwargs = build_token_store()._client.connection_pool.connection_kwargs
    assert kwargs["username"] == "plane"
    assert kwargs["password"] == "pw"


def test_password_path_without_username_is_unchanged(monkeypatch, no_ping):
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    assert build_token_store()._client.connection_pool.connection_kwargs["username"] is None
