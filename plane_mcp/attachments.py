"""Attachment helpers shared by both tool surfaces.

Network limits, MIME allow-lists, the SSRF guard and the attachment
normaliser. Kept out of either surface package so neither depends on the other.
"""

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

# ── Limits ────────────────────────────────────────────────────────────────────
IMAGE_READ_LIMIT = 5 * 1024 * 1024  # 5 MB
TEXT_READ_LIMIT = 1 * 1024 * 1024  # 1 MB
UPLOAD_SIZE_LIMIT = 5 * 1024 * 1024  # 5 MB

# Connect timeout / read timeout tuple used for all outbound HTTP calls.
HTTP_TIMEOUT = (10, 60)

# ── Supported MIME types ──────────────────────────────────────────────────────
READABLE_IMAGE_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
READABLE_TEXT_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "text/xml",
        "text/yaml",
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
    }
)

# ── SSRF guard ───────────────────────────────────────────────────────────────
# Networks the stdlib ipaddress flags do not reliably classify on their own, so
# we list them explicitly and fail closed. Mirrors the core product's
# plane.utils.ip_address so both surfaces block the same targets.
MAX_REDIRECTS = 5

_BLOCKED_NETWORKS = [
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",  # "this host" / unspecified
        "100.64.0.0/10",  # carrier-grade NAT (RFC 6598)
        "169.254.0.0/16",  # link-local / cloud metadata (169.254.169.254)
        "255.255.255.255/32",  # broadcast
        "::ffff:0:0/96",  # IPv4-mapped IPv6  (e.g. ::ffff:169.254.169.254)
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # NAT64 local-use
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
        "fec0::/10",  # deprecated site-local
    )
]


def _embedded_ipv4(ip: ipaddress._BaseAddress):
    """Yield any IPv4 address embedded inside an IPv6 transition address.

    ``::ffff:169.254.169.254`` looks like a public IPv6 address to the stdlib
    flags, but the packet ultimately reaches the embedded IPv4 target, so it
    must be validated too. Covers IPv4-mapped, 6to4, Teredo and NAT64.
    """
    if ip.version != 6:
        return
    if ip.ipv4_mapped is not None:
        yield ip.ipv4_mapped
    if ip.sixtofour is not None:
        yield ip.sixtofour
    teredo = ip.teredo
    if teredo is not None:
        yield teredo[0]
        yield teredo[1]
    if ip in ipaddress.ip_network("64:ff9b::/96"):
        yield ipaddress.ip_address(int(ip) & 0xFFFFFFFF)


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """True if an IP must never be an outbound target. Fails closed."""
    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if any(ip.version == net.version and ip in net for net in _BLOCKED_NETWORKS):
        return True
    return any(_is_blocked_ip(embedded) for embedded in _embedded_ipv4(ip))


def assert_public_url(url: str) -> None:
    """Raise ValueError unless every address the URL resolves to is public.

    Checks *all* resolved addresses (not just the first) and decodes IPv6
    transition formats, so ``http://[::ffff:169.254.169.254]/`` is blocked.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are allowed: {url!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL (no hostname): {url!r}")

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname {hostname!r}: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise ValueError(
                f"URL {url!r} resolves to a private/reserved address ({ip}) "
                "and cannot be fetched for security reasons."
            )


def fetch_public_file(url: str, max_bytes: int) -> tuple[bytes, Any]:
    """GET a caller-supplied URL with SSRF protection, returning (body, headers).

    Validates the target before every hop, follows redirects manually (a public
    URL cannot 302 to an internal one), and streams the body with a hard size
    cap so an oversized response cannot exhaust memory before a length check.
    """
    for _ in range(MAX_REDIRECTS + 1):
        assert_public_url(url)
        response = requests.get(url, timeout=HTTP_TIMEOUT, allow_redirects=False, stream=True)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError(f"Redirect without a Location header from {url!r}")
            url = urljoin(url, location)
            continue
        try:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(8192):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"File at {url!r} exceeds the {max_bytes // 1024 // 1024} MB limit.")
                chunks.append(chunk)
            return b"".join(chunks), response.headers
        finally:
            response.close()
    raise ValueError(f"Too many redirects while fetching {url!r}")


def attachment_to_dict(attachment: Any, workspace_slug: str) -> dict[str, Any]:
    data = attachment.model_dump()
    attrs = data.get("attributes") or {}
    data["name"] = attrs.get("name")
    data["size"] = attrs.get("size") or data.get("size")
    data["content_type"] = attrs.get("type")
    return data
