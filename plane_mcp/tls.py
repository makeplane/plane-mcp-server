"""Keep the TLS CA bundle readable for the whole life of the process.

`uvx plane-mcp-server` runs the server from an environment inside uv's cache.
uv may delete that environment while the server is still running (`uv cache
prune`, `uv cache clean`, or a newer resolution replacing it). `requests`
re-checks its CA bundle path (certifi's `cacert.pem`, inside that environment)
on every request, and the Plane SDK opens a fresh session per tool call, so
once the file is gone every call fails with "Could not find a suitable TLS CA
certificate bundle, invalid path: ...". Copying the bundle out of the
environment at startup avoids that.
"""

import hashlib
import logging
import os
import tempfile
from pathlib import Path

import certifi
import requests.utils

logger = logging.getLogger(__name__)

# Variables that already tell requests/httpx/OpenSSL which bundle to use.
_CA_ENV_VARS = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "plane-mcp-server"


def pin_ca_bundle(cache_dir: Path | None = None) -> Path | None:
    """Copy certifi's bundle to a stable path and point requests and httpx at it.

    Does nothing when the user already chose a bundle via the environment.
    Returns the pinned path, or None when nothing was changed. Never raises:
    on failure it logs and leaves the default behaviour in place.
    """
    if any(os.environ.get(name) for name in _CA_ENV_VARS):
        return None
    try:
        data = Path(certifi.where()).read_bytes()
        target_dir = cache_dir or _cache_dir()
        target = target_dir / f"cacert-{hashlib.sha256(data).hexdigest()[:16]}.pem"
        if not target.is_file() or target.read_bytes() != data:
            target_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".cacert-", suffix=".pem")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, target)
            except BaseException:
                os.unlink(tmp)
                raise
    except OSError as exc:
        logger.warning("Could not pin the TLS CA bundle outside the package environment: %s", exc)
        return None
    # requests (the Plane SDK) reads REQUESTS_CA_BUNDLE; httpx reads SSL_CERT_FILE.
    os.environ["REQUESTS_CA_BUNDLE"] = str(target)
    os.environ["SSL_CERT_FILE"] = str(target)
    return target


def ca_bundle_path() -> str:
    """The CA bundle for code that builds urllib3 pools directly."""
    return os.environ.get("REQUESTS_CA_BUNDLE") or requests.utils.DEFAULT_CA_BUNDLE_PATH
