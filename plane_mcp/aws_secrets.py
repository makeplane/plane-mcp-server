"""AWS Secrets Manager helpers for Redis auth token rotation.

Mirrors the pattern in plane-ee/apps/api/plane/utils/aws_secrets.py — TTL-cached
``get_secret`` keyed by (arn, region), plus a redis-py ``CredentialProvider``
that reads the current password from the cache on every new Redis connection.
"""

import json
import logging
import os
import threading
import time

import boto3
from redis import CredentialProvider

_SECRET_CACHE: dict[tuple[str, str], dict] = {}
_cache_lock = threading.Lock()
_logger = logging.getLogger(__name__)
_DEFAULT_TTL = int(os.environ.get("AWS_SECRET_CACHE_TTL", 300))


def get_secret(secret_arn: str, region: str, force_refresh: bool = False) -> dict:
    """Fetch and TTL-cache a secret from AWS Secrets Manager.

    Returns a copy of the parsed JSON secret dict so callers cannot mutate the
    cached value.
    """
    cache_key = (secret_arn, region)
    with _cache_lock:
        if not force_refresh and cache_key in _SECRET_CACHE:
            entry = _SECRET_CACHE[cache_key]
            if time.time() - entry["fetched_at"] < _DEFAULT_TTL:
                return entry["value"].copy()
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_arn)
        value = json.loads(response["SecretString"])
        _SECRET_CACHE[cache_key] = {"value": value, "fetched_at": time.time()}
        _logger.info(
            "Refreshed secret from Secrets Manager",
            extra={"secret_arn": secret_arn},
        )
        return value.copy()


class ElastiCacheCredentialProvider(CredentialProvider):
    """redis-py credential provider that resolves the password from Secrets Manager.

    ``get_credentials`` is called by redis-py during AUTH on each new connection,
    so as long as the TTL cache returns a refreshed value after ``AWS_SECRET_CACHE_TTL``
    seconds, rotated ElastiCache auth tokens are picked up automatically without
    restarting the server.
    """

    def __init__(self, secret_arn: str, region: str, token_key: str, username: str | None = None):
        self._arn = secret_arn
        self._region = region
        self._key = token_key
        self._username = username

    def get_credentials(self) -> tuple[str] | tuple[str, str]:
        secret = get_secret(self._arn, self._region)
        password = secret.get(self._key)
        if password is None:
            raise RuntimeError(f"REDIS_AUTH_TOKEN_KEY {self._key!r} not present in secret {self._arn}")
        return (self._username, password) if self._username else (password,)
