"""pin_ca_bundle keeps TLS working after the package environment's certifi is deleted."""

import os
import shutil
from pathlib import Path

import certifi
import pytest
import requests

from plane_mcp import tls


@pytest.fixture
def clean_env(monkeypatch):
    # pin_ca_bundle writes os.environ directly; snapshot it so nothing leaks.
    saved = dict(os.environ)
    for name in tls._CA_ENV_VARS:
        os.environ.pop(name, None)
    yield monkeypatch
    os.environ.clear()
    os.environ.update(saved)


def test_pins_a_copy_and_points_requests_and_httpx_at_it(clean_env, tmp_path):
    pinned = tls.pin_ca_bundle(cache_dir=tmp_path)

    assert pinned is not None and pinned.parent == tmp_path
    assert pinned.read_bytes() == Path(certifi.where()).read_bytes()
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(pinned)
    assert os.environ["SSL_CERT_FILE"] == str(pinned)
    assert tls.ca_bundle_path() == str(pinned)
    # A fresh process (env not yet set) reuses the same file.
    for name in tls._CA_ENV_VARS:
        os.environ.pop(name, None)
    assert tls.pin_ca_bundle(cache_dir=tmp_path) == pinned
    assert len(list(tmp_path.iterdir())) == 1


def test_respects_a_bundle_the_user_already_chose(clean_env, tmp_path):
    clean_env.setenv("SSL_CERT_FILE", "/etc/ssl/custom.pem")

    assert tls.pin_ca_bundle(cache_dir=tmp_path) is None
    assert "REQUESTS_CA_BUNDLE" not in os.environ
    assert list(tmp_path.iterdir()) == []


def test_unwritable_cache_dir_falls_back_without_raising(clean_env, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")

    assert tls.pin_ca_bundle(cache_dir=blocker / "sub") is None
    assert "REQUESTS_CA_BUNDLE" not in os.environ


def test_requests_survives_deleted_certifi(clean_env, tmp_path):
    """The failure mode from uv pruning the environment, without the network."""
    fake_certifi = tmp_path / "env" / "cacert.pem"
    fake_certifi.parent.mkdir()
    shutil.copy(certifi.where(), fake_certifi)
    clean_env.setattr(tls.certifi, "where", lambda: str(fake_certifi))
    clean_env.setattr(requests.adapters, "DEFAULT_CA_BUNDLE_PATH", str(fake_certifi))

    tls.pin_ca_bundle(cache_dir=tmp_path / "pinned")
    shutil.rmtree(fake_certifi.parent)

    session = requests.Session()
    settings = session.merge_environment_settings("https://api.plane.so", {}, None, True, None)
    adapter = requests.adapters.HTTPAdapter()
    conn = adapter.get_connection_with_tls_context(
        requests.Request("GET", "https://api.plane.so").prepare(), settings["verify"]
    )
    # Raises "Could not find a suitable TLS CA certificate bundle" without the pin.
    adapter.cert_verify(conn, "https://api.plane.so", settings["verify"], None)
    assert conn.ca_certs == os.environ["REQUESTS_CA_BUNDLE"]


def test_ca_pool_kwargs_uses_ca_certs_for_a_file(clean_env, tmp_path):
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    assert tls.ca_pool_kwargs() == {"ca_certs": str(bundle)}


def test_ca_pool_kwargs_uses_ca_cert_dir_for_a_directory(clean_env, tmp_path):
    os.environ["REQUESTS_CA_BUNDLE"] = str(tmp_path)
    assert tls.ca_pool_kwargs() == {"ca_cert_dir": str(tmp_path)}


class _Captured(Exception):
    """Raised by the fake pool once it has recorded its kwargs."""


@pytest.mark.parametrize("as_dir", [False, True], ids=["file", "directory"])
def test_attachment_pools_pass_the_ca_path_in_the_right_kwarg(clean_env, tmp_path, as_dir):
    from plane_mcp import attachments

    if as_dir:
        ca = tmp_path
        expected = {"ca_cert_dir": str(tmp_path)}
    else:
        ca = tmp_path / "bundle.pem"
        ca.write_bytes(Path(certifi.where()).read_bytes())
        expected = {"ca_certs": str(ca)}
    os.environ["REQUESTS_CA_BUNDLE"] = str(ca)

    seen = []

    def fake_pool(*_args, **kwargs):
        seen.append(kwargs)
        raise _Captured

    # Direct path: skip DNS and the private-IP check, capture the pool kwargs.
    clean_env.setattr(attachments, "_select_proxy_url", lambda _url: None)
    clean_env.setattr(attachments, "_resolve_and_validate", lambda _host, _port: "93.184.216.34")
    clean_env.setattr(attachments.urllib3, "HTTPSConnectionPool", fake_pool)
    with pytest.raises(_Captured):
        attachments._fetch_one_hop("https://example.com/a.png", 1024)

    # Proxy path: capture the ProxyManager kwargs.
    clean_env.setattr(attachments.urllib3, "ProxyManager", fake_pool)
    with pytest.raises(_Captured):
        attachments._fetch_one_hop_via_proxy("https://example.com/a.png", "http://proxy.invalid:3128", 1024)

    assert len(seen) == 2
    for kwargs in seen:
        assert {k: kwargs[k] for k in ("ca_certs", "ca_cert_dir") if k in kwargs} == expected
