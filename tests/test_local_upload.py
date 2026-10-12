"""The local helper sends bounded bytes without Plane or netrc credentials."""

from __future__ import annotations

import io
import json
from unittest.mock import Mock

import pytest
import requests

from plane_mcp import local_upload
from plane_mcp.attachments import UPLOAD_SIZE_LIMIT

PNG = b"\x89PNG\r\n\x1a\nsynthetic-payload"
SECRET = "signed-policy-secret"
ATTACHMENT = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    instructions = {
        "attachment_id": ATTACHMENT,
        "name": path.name,
        "content_type": "image/png",
        "size": len(PNG),
        "upload_data": {"url": "https://storage.example/", "fields": {"policy": SECRET, "key": "shot.png"}},
    }
    sent = []

    def send(session, request, **kwargs):
        sent.append((session, request, kwargs))
        response = requests.Response()
        response.status_code = 204
        response._content = b""
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return path, instructions, sent


def test_success_sends_bytes_and_all_signed_fields_without_credentials(transfer, monkeypatch):
    path, instructions, sent = transfer
    monkeypatch.setenv("PLANE_API_KEY", "plane-api-secret")
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", Mock(side_effect=AssertionError("netrc must be disabled")))
    result = local_upload.upload_file(path, instructions)
    assert result == {"attachment_id": ATTACHMENT, "transfer_succeeded": True}
    session, request, kwargs = sent[0]
    assert session.trust_env is False
    assert request.method == "POST"
    assert PNG in request.body and SECRET.encode() in request.body
    assert b"plane-api-secret" not in request.body
    assert "Authorization" not in request.headers and "X-Api-Key" not in request.headers
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == local_upload.HTTP_TIMEOUT


@pytest.mark.parametrize("status", [302, 400, 401, 403, 500])
def test_transfer_failure_never_returns_success(transfer, monkeypatch, status):
    path, instructions, _ = transfer

    def send(*args, **kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = SECRET.encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    with pytest.raises(ValueError, match="[Dd]o not complete") as error:
        local_upload.upload_file(path, instructions)
    assert SECRET not in str(error.value)


def test_timeout_is_uncertain_and_does_not_expose_request(transfer, monkeypatch):
    path, instructions, _ = transfer
    monkeypatch.setattr(requests.Session, "send", Mock(side_effect=requests.Timeout(SECRET)))
    with pytest.raises(ValueError, match="uncertain") as error:
        local_upload.upload_file(path, instructions)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("size", [0, -1, UPLOAD_SIZE_LIMIT + 1, True, len(PNG) + 1])
def test_size_validation_prevents_transfer(transfer, size):
    path, instructions, sent = transfer
    instructions["size"] = size
    with pytest.raises(ValueError):
        local_upload.upload_file(path, instructions)
    assert not sent


def test_oversized_metadata_is_refused_before_file_read(transfer, monkeypatch):
    path, instructions, sent = transfer
    instructions["size"] = UPLOAD_SIZE_LIMIT + 1
    monkeypatch.setattr(local_upload.os, "open", Mock(side_effect=AssertionError("file must not be read")))
    with pytest.raises(ValueError):
        local_upload.upload_file(path, instructions)
    assert not sent


@pytest.mark.parametrize("url", ["http://storage.example/", "https://user:secret@storage.example/", "file:///tmp/file"])
def test_unsafe_storage_urls_are_refused(transfer, url):
    path, instructions, sent = transfer
    instructions["upload_data"]["url"] = url
    with pytest.raises(ValueError, match="Invalid upload instructions"):
        local_upload.upload_file(path, instructions)
    assert not sent


def test_plane_auth_fields_are_refused(transfer):
    path, instructions, sent = transfer
    instructions["upload_data"]["fields"]["Authorization"] = "Bearer secret"
    with pytest.raises(ValueError):
        local_upload.upload_file(path, instructions)
    assert not sent


def test_cli_prints_only_transfer_receipt(transfer, monkeypatch, capsys):
    path, instructions, _ = transfer
    monkeypatch.setattr(local_upload.sys, "argv", ["local_upload", str(path)])
    monkeypatch.setattr(local_upload.sys, "stdin", io.StringIO(json.dumps(instructions)))
    local_upload.main()
    out = capsys.readouterr()
    assert json.loads(out.out) == {"attachment_id": ATTACHMENT, "transfer_succeeded": True}
    assert not out.err and SECRET not in out.out and "PNG" not in out.out


def test_cli_bad_json_never_prints_signed_input(transfer, monkeypatch, capsys):
    path, _, _ = transfer
    monkeypatch.setattr(local_upload.sys, "argv", ["local_upload", str(path)])
    monkeypatch.setattr(local_upload.sys, "stdin", io.StringIO(f'{{"{SECRET}"'))
    with pytest.raises(SystemExit) as error:
        local_upload.main()
    assert error.value.code == 1
    out = capsys.readouterr()
    assert SECRET not in out.err and not out.out


def test_changed_file_read_is_bounded_and_prevents_transfer(transfer, monkeypatch):
    from contextlib import contextmanager

    path, instructions, sent = transfer
    original_fdopen = local_upload.os.fdopen
    read = Mock(return_value=PNG + b"extra")

    @contextmanager
    def changed(fd, mode):
        with original_fdopen(fd, mode) as source:

            class ChangingFile:
                fileno = source.fileno

                def read(self, bound):
                    return read(bound)

            yield ChangingFile()

    monkeypatch.setattr(local_upload.os, "fdopen", changed)
    with pytest.raises(ValueError, match="changed"):
        local_upload.upload_file(path, instructions)
    read.assert_called_once_with(UPLOAD_SIZE_LIMIT + 1)
    assert not sent


def test_missing_local_file_does_not_transfer(transfer):
    path, instructions, sent = transfer
    path.unlink()
    with pytest.raises(ValueError, match="Cannot read"):
        local_upload.upload_file(path, instructions)
    assert not sent


@pytest.mark.skipif(not hasattr(local_upload.os, "mkfifo"), reason="Named pipes are POSIX-only")
def test_nonregular_file_does_not_transfer(transfer):
    path, instructions, sent = transfer
    path.unlink()
    local_upload.os.mkfifo(path)
    with pytest.raises(ValueError, match="not regular"):
        local_upload.upload_file(path, instructions)
    assert not sent


@pytest.mark.parametrize(
    "upload_data",
    [
        {"url": "https://storage.example/", "fields": {}},
        {"url": "https://storage.example:invalid/", "fields": {"policy": SECRET}},
        {"url": "https://storage.example/", "fields": {"file": SECRET}},
    ],
)
def test_malformed_instructions_are_refused_before_open(transfer, monkeypatch, upload_data):
    path, instructions, sent = transfer
    instructions["upload_data"] = upload_data
    monkeypatch.setattr(local_upload.os, "open", Mock(side_effect=AssertionError("must not read file")))
    with pytest.raises(ValueError, match="Invalid upload instructions"):
        local_upload.upload_file(path, instructions)
    assert not sent


def test_cli_encoding_error_does_not_expose_input(transfer, monkeypatch, capsys):
    path, _, _ = transfer
    monkeypatch.setattr(local_upload.sys, "argv", ["local_upload", str(path)])
    monkeypatch.setattr(
        local_upload.sys,
        "stdin",
        Mock(read=Mock(side_effect=UnicodeDecodeError("utf-8", SECRET.encode(), 0, 1, "invalid"))),
    )
    with pytest.raises(SystemExit):
        local_upload.main()
    assert SECRET not in capsys.readouterr().err


def test_portable_open_flags(transfer, monkeypatch):
    path, instructions, _ = transfer
    real_open = local_upload.os.open
    flags_used = []

    def checked_open(path, flags):
        flags_used.append(flags)
        return real_open(path, flags)

    monkeypatch.delattr(local_upload.os, "O_NONBLOCK", raising=False)
    monkeypatch.setattr(local_upload.os, "O_BINARY", 0, raising=False)
    monkeypatch.setattr(local_upload.os, "open", checked_open)
    assert local_upload.upload_file(path, instructions)["transfer_succeeded"] is True
    assert flags_used == [local_upload.os.O_RDONLY]
