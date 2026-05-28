"""Unit tests for plane_mcp.aws_secrets and the Redis token-store resolver."""

import json

import boto3
import pytest
from botocore.stub import Stubber
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore

from plane_mcp import aws_secrets
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


def test_build_token_store_host_port_only_returns_redis(monkeypatch):
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    _forbid_boto3(monkeypatch)
    assert isinstance(_build_token_store(), RedisStore)


def test_build_token_store_password_wins_over_arn(monkeypatch):
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


def test_build_token_store_arn_without_irsa_skips_sm(monkeypatch, caplog):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    _forbid_boto3(monkeypatch)

    with caplog.at_level("WARNING", logger="plane_mcp.server"):
        store = _build_token_store()

    assert isinstance(store, RedisStore)
    assert any("IRSA/Pod Identity" in rec.message for rec in caplog.records)


def test_build_token_store_arn_with_aws_role_arn_activates_sm(monkeypatch):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    stubber = _stub_secrets(monkeypatch, [{"authToken": "secret-pw"}])
    assert isinstance(_build_token_store(), RedisStore)
    stubber.assert_no_pending_responses()


def test_build_token_store_arn_with_pod_identity_activates_sm(monkeypatch):
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


def test_build_token_store_custom_token_key(monkeypatch):
    monkeypatch.setenv("ELASTICACHE_SECRET_ARN", ARN)
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/test")
    monkeypatch.setenv("REDIS_AUTH_TOKEN_KEY", "token")
    _stub_secrets(monkeypatch, [{"token": "secret-pw"}])
    assert isinstance(_build_token_store(), RedisStore)
