"""Unit tests for plane_mcp.aws_secrets and the Redis token-store resolver."""

import json
import logging
import time

import boto3
import pytest
from botocore.stub import Stubber
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore

from plane_mcp import aws_secrets, server
from plane_mcp.aws_secrets import ElastiCacheCredentialProvider, get_secret
from plane_mcp.server import _build_token_store

ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"
REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    aws_secrets._SECRET_CACHE.clear()
    for k in (
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_PASSWORD",
        "REDIS_SSL",
        "ELASTICACHE_SECRET_ARN",
        "REDIS_AUTH_TOKEN_KEY",
        "AWS_REGION",
        "AWS_ROLE_ARN",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(k, raising=False)
    yield
    aws_secrets._SECRET_CACHE.clear()


def _stub_secrets(monkeypatch, payloads: list[dict]):
    """Patch boto3.client to return a Stubber-backed Secrets Manager client.

    Each entry in ``payloads`` becomes one queued ``get_secret_value`` response;
    unexpected calls raise.
    """
    client = boto3.client("secretsmanager", region_name=REGION)
    stubber = Stubber(client)
    for payload in payloads:
        stubber.add_response(
            "get_secret_value",
            {"SecretString": json.dumps(payload)},
            expected_params={"SecretId": ARN},
        )
    stubber.activate()
    monkeypatch.setattr(aws_secrets.boto3, "client", lambda *a, **k: client)
    return stubber


def _forbid_boto3(monkeypatch):
    def _explode(*_a, **_k):
        raise AssertionError("Unexpected boto3.client() call")

    monkeypatch.setattr(aws_secrets.boto3, "client", _explode)


@pytest.fixture
def no_verify(monkeypatch):
    """Skip the eager Redis PING so store-resolution tests need no live Redis."""
    monkeypatch.setattr(server, "_verify_redis_connection", lambda store: None)


@pytest.fixture
def capture_log(caplog):
    """Capture records from a fastmcp-namespaced logger.

    The ``fastmcp`` logger sets ``propagate=False``, so caplog's root handler
    never sees these records — attach it directly to the target logger instead.
    """

    def _capture(logger_name: str):
        target = logging.getLogger(logger_name)
        target.addHandler(caplog.handler)
        target.setLevel(logging.WARNING)
        return caplog

    return _capture


def test_get_secret_caches_within_ttl(monkeypatch):
    stubber = _stub_secrets(monkeypatch, [{"authToken": "p1"}])
    assert get_secret(ARN, REGION) == {"authToken": "p1"}
    assert get_secret(ARN, REGION) == {"authToken": "p1"}
    stubber.assert_no_pending_responses()


def test_get_secret_refreshes_after_ttl(monkeypatch):
    stubber = _stub_secrets(monkeypatch, [{"authToken": "p1"}, {"authToken": "p2"}])
    monkeypatch.setattr(aws_secrets, "_DEFAULT_TTL", 0)
    assert get_secret(ARN, REGION)["authToken"] == "p1"
    assert get_secret(ARN, REGION)["authToken"] == "p2"
    stubber.assert_no_pending_responses()


def test_get_secret_force_refresh(monkeypatch):
    stubber = _stub_secrets(monkeypatch, [{"authToken": "p1"}, {"authToken": "p2"}])
    assert get_secret(ARN, REGION)["authToken"] == "p1"
    assert get_secret(ARN, REGION, force_refresh=True)["authToken"] == "p2"
    stubber.assert_no_pending_responses()


def test_get_secret_returns_copy(monkeypatch):
    _stub_secrets(monkeypatch, [{"authToken": "p1"}])
    first = get_secret(ARN, REGION)
    first["authToken"] = "mutated"
    second = get_secret(ARN, REGION)
    assert second["authToken"] == "p1"


def test_parse_default_ttl_valid(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_CACHE_TTL", "60")
    assert aws_secrets._parse_default_ttl() == 60


def test_parse_default_ttl_unset(monkeypatch):
    monkeypatch.delenv("AWS_SECRET_CACHE_TTL", raising=False)
    assert aws_secrets._parse_default_ttl() == 300


def test_parse_default_ttl_invalid_falls_back(monkeypatch, capture_log):
    caplog = capture_log("fastmcp.plane_mcp.aws_secrets")
    monkeypatch.setenv("AWS_SECRET_CACHE_TTL", "not-a-number")
    assert aws_secrets._parse_default_ttl() == 300
    assert any("not an integer" in rec.message for rec in caplog.records)


def test_get_secret_concurrent_race_uses_existing_fresh_entry(monkeypatch):
    """If another thread populates the cache while we're fetching, prefer the
    existing fresh entry rather than overwriting it."""
    stubber = _stub_secrets(monkeypatch, [{"authToken": "ours"}])

    # Simulate a racing thread that wrote a fresh entry while our fetch was in
    # flight, by pre-populating the cache after we've passed the fast-path check
    # but before the write-back. We do that by patching json.loads to inject.
    original_loads = aws_secrets.json.loads

    def _injecting_loads(s):
        # Another thread "won" the race and wrote first.
        aws_secrets._SECRET_CACHE[(ARN, REGION)] = {
            "value": {"authToken": "theirs"},
            "fetched_at": time.time(),
        }
        return original_loads(s)

    monkeypatch.setattr(aws_secrets.json, "loads", _injecting_loads)

    result = get_secret(ARN, REGION)
    assert result == {"authToken": "theirs"}
    assert aws_secrets._SECRET_CACHE[(ARN, REGION)]["value"] == {"authToken": "theirs"}
    stubber.assert_no_pending_responses()


def test_get_secret_force_refresh_overwrites_race_winner(monkeypatch):
    """force_refresh=True always installs the freshly-fetched value, ignoring
    any racing writer."""
    stubber = _stub_secrets(monkeypatch, [{"authToken": "forced"}])

    original_loads = aws_secrets.json.loads

    def _injecting_loads(s):
        aws_secrets._SECRET_CACHE[(ARN, REGION)] = {
            "value": {"authToken": "stale-racer"},
            "fetched_at": time.time(),
        }
        return original_loads(s)

    monkeypatch.setattr(aws_secrets.json, "loads", _injecting_loads)

    result = get_secret(ARN, REGION, force_refresh=True)
    assert result == {"authToken": "forced"}
    assert aws_secrets._SECRET_CACHE[(ARN, REGION)]["value"] == {"authToken": "forced"}
    stubber.assert_no_pending_responses()


def test_credential_provider_returns_password(monkeypatch):
    _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    provider = ElastiCacheCredentialProvider(ARN, REGION, "authToken")
    assert provider.get_credentials() == ("secret-pw",)


def test_credential_provider_with_username(monkeypatch):
    _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    provider = ElastiCacheCredentialProvider(ARN, REGION, "authToken", username="alice")
    assert provider.get_credentials() == ("alice", "secret-pw")


def test_credential_provider_missing_key_raises(monkeypatch):
    _stub_secrets(monkeypatch, [{"otherKey": "value"}])
    provider = ElastiCacheCredentialProvider(ARN, REGION, "authToken")
    with pytest.raises(RuntimeError, match="authToken"):
        provider.get_credentials()


def test_build_token_store_no_env_returns_memory_store():
    assert isinstance(_build_token_store(), MemoryStore)


def test_build_token_store_host_port_only_returns_redis(monkeypatch, no_verify):
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    _forbid_boto3(monkeypatch)
    assert isinstance(_build_token_store(), RedisStore)


def test_build_token_store_password_wins_over_arn(monkeypatch, no_verify):
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    _forbid_boto3(monkeypatch)
    assert isinstance(_build_token_store(), RedisStore)


def test_build_token_store_password_without_host_port_raises(monkeypatch):
    monkeypatch.setenv("REDIS_PASSWORD", "pw")
    with pytest.raises(RuntimeError, match="REDIS_PASSWORD"):
        _build_token_store()


def test_build_token_store_arn_without_host_port_raises(monkeypatch):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    with pytest.raises(RuntimeError, match="ELASTICACHE_SECRET_ARN"):
        _build_token_store()


def test_build_token_store_arn_without_irsa_skips_sm(monkeypatch, capture_log, no_verify):
    caplog = capture_log("fastmcp.plane_mcp.server")
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    _forbid_boto3(monkeypatch)

    store = _build_token_store()

    assert isinstance(store, RedisStore)
    assert any("IRSA/Pod Identity" in rec.message for rec in caplog.records)


def test_build_token_store_arn_with_aws_role_arn_activates_sm(monkeypatch, no_verify):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    stubber = _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    assert isinstance(_build_token_store(), RedisStore)
    stubber.assert_no_pending_responses()


def test_build_token_store_arn_with_pod_identity_activates_sm(monkeypatch, no_verify):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://169.254.170.23/v1/credentials")
    stubber = _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    assert isinstance(_build_token_store(), RedisStore)
    stubber.assert_no_pending_responses()


def test_build_token_store_arn_missing_token_key_raises(monkeypatch):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    _stub_secrets(monkeypatch, [{"otherKey": "value"}])
    with pytest.raises(RuntimeError, match="REDIS_AUTH_TOKEN_KEY"):
        _build_token_store()


def test_build_token_store_custom_token_key(monkeypatch, no_verify):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    monkeypatch.setenv("REDIS_AUTH_TOKEN_KEY", "token")
    _stub_secrets(monkeypatch, [{"token": "secret-pw"}])
    assert isinstance(_build_token_store(), RedisStore)


def _capture_async_redis_kwargs(monkeypatch) -> dict:
    """Patch redis.asyncio.Redis to record the kwargs the secret path builds it with."""
    import redis.asyncio as aioredis

    captured: dict = {}

    class _FakeAsyncRedis:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(aioredis, "Redis", _FakeAsyncRedis)
    return captured


def test_build_token_store_secret_path_uses_tls_by_default(monkeypatch, no_verify):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    captured = _capture_async_redis_kwargs(monkeypatch)

    _build_token_store()
    assert captured["ssl"] is True


def test_build_token_store_secret_path_ssl_can_be_disabled(monkeypatch, no_verify):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    monkeypatch.setenv("REDIS_SSL", "false")
    _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    captured = _capture_async_redis_kwargs(monkeypatch)

    _build_token_store()
    assert captured["ssl"] is False


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_redis_ssl_enabled(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("REDIS_SSL", raising=False)
    else:
        monkeypatch.setenv("REDIS_SSL", value)
    assert server._redis_ssl_enabled() is expected


def test_verify_redis_connection_succeeds():
    class _Client:
        async def ping(self):
            return True

    class _Store:
        _client = _Client()

    server._verify_redis_connection(_Store())  # should not raise


def test_verify_redis_connection_raises_on_failure():
    class _Client:
        async def ping(self):
            raise OSError("connection refused")

    class _Store:
        _client = _Client()

    with pytest.raises(RuntimeError, match="Redis connection failed during startup PING"):
        server._verify_redis_connection(_Store())
