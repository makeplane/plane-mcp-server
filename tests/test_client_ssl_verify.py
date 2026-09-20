"""Unit tests for PLANE_SSL_VERIFY parsing in plane_mcp.client._resolve_ssl_verify.

Pure function, no network and no live Plane instance required.
"""

import pytest

from plane_mcp.client import _resolve_ssl_verify


def test_default_is_true_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANE_SSL_VERIFY", raising=False)
    assert _resolve_ssl_verify("https://plane.example.com") is True


@pytest.mark.parametrize("raw", ["false", "False", "FALSE", "0", "no", "No"])
def test_falsy_values_disable_verification(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("PLANE_SSL_VERIFY", raw)
    assert _resolve_ssl_verify("https://plane.example.com") is False


def test_existing_file_path_is_passed_through_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    ca_bundle = tmp_path / "internal-ca.pem"
    ca_bundle.write_text("fake cert content")
    monkeypatch.setenv("PLANE_SSL_VERIFY", str(ca_bundle))
    assert _resolve_ssl_verify("https://plane.example.com") == str(ca_bundle)


def test_nonexistent_path_falls_back_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANE_SSL_VERIFY", "/no/such/path/ca.pem")
    assert _resolve_ssl_verify("https://plane.example.com") is True


def test_empty_string_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANE_SSL_VERIFY", "")
    assert _resolve_ssl_verify("https://plane.example.com") is True
