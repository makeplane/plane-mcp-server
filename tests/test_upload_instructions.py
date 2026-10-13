"""Execute the returned client example. Check the bytes and failure boundaries."""

import http.client
import io
import json
import sys
from unittest.mock import Mock

import pytest

from plane_mcp.attachments import UPLOAD_SIZE_LIMIT
from plane_mcp.upload_instructions import build_upload_instructions

ATTACHMENT = "33333333-3333-4333-8333-333333333333"
SECRET = "signed-example-secret"
PNG = b"\x89PNG\r\n\x1a\nsynthetic-payload"


@pytest.fixture
def example(tmp_path, monkeypatch):
    path = tmp_path / "shot.png"
    path.write_bytes(PNG)
    guide = build_upload_instructions("project", "workitem", ATTACHMENT)
    result = {
        "attachment_id": ATTACHMENT,
        "name": 'shot".png',
        "content_type": "image/png",
        "size": len(PNG),
        "upload_data": {
            "url": f"https://storage.example/upload?signature={SECRET}",
            "fields": {"policy": SECRET, "key": "shot.png", "Content-Type": "image/png"},
        },
        "instructions": guide,
    }
    connection = Mock()
    connection.getresponse.return_value.status = 204
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(http.client, "HTTPSConnection", constructor)
    monkeypatch.setattr(sys, "argv", ["upload.py", str(path)])
    return result, guide["direct_upload"]["python_stdlib"]["code"], constructor, connection


def execute(result, code, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(result)))
    exec(compile(code, "<returned-upload-example>", "exec"), {"__name__": "__main__"})


def test_example_sends_exact_parts_without_credentials_or_redirects(example, monkeypatch, capsys):
    result, code, constructor, connection = example
    monkeypatch.setenv("HTTPS_PROXY", f"https://{SECRET}@proxy.example/")
    monkeypatch.setenv("PLANE_API_KEY", "plane-api-secret")
    execute(result, code, monkeypatch)
    constructor.assert_called_once_with("storage.example", 443, timeout=10)
    connection.sock.settimeout.assert_called_once_with(60)
    connection.request.assert_called_once()
    args, kwargs = connection.request.call_args
    assert args == ("POST", f"/upload?signature={SECRET}")
    assert list(kwargs["headers"]) == ["Content-Type"]
    body = kwargs["body"]
    assert PNG in body and SECRET.encode() in body
    assert b'filename="shot\\".png"' in body
    for key, value in result["upload_data"]["fields"].items():
        assert f'name="{key}"'.encode() in body
        assert value.encode() in body
    assert b"plane-api-secret" not in body
    connection.close.assert_called_once()
    output = capsys.readouterr()
    assert json.loads(output.out) == {"attachment_id": ATTACHMENT, "transfer_succeeded": True, "http_status": 204}
    assert not output.err and SECRET not in output.out


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 500])
def test_example_rejects_redirects_and_errors_without_success(example, monkeypatch, capsys, status):
    result, code, _, connection = example
    connection.getresponse.return_value.status = status
    with pytest.raises(SystemExit) as error:
        execute(result, code, monkeypatch)
    assert error.value.code == 1
    connection.request.assert_called_once()
    connection.close.assert_called_once()
    output = capsys.readouterr()
    assert not output.out and "Do not complete" in output.err
    assert SECRET not in output.err


def test_example_sanitizes_uncertain_transfer(example, monkeypatch, capsys):
    result, code, _, connection = example
    connection.getresponse.side_effect = TimeoutError(SECRET)
    with pytest.raises(SystemExit):
        execute(result, code, monkeypatch)
    output = capsys.readouterr()
    assert not output.out and "uncertain" in output.err
    assert SECRET not in output.err
    connection.close.assert_called_once()


@pytest.mark.parametrize("size", [0, True, UPLOAD_SIZE_LIMIT + 1, len(PNG) + 1])
def test_example_rejects_size_before_network(example, monkeypatch, size):
    result, code, constructor, _ = example
    result["size"] = size
    with pytest.raises(SystemExit):
        execute(result, code, monkeypatch)
    constructor.assert_not_called()


def test_example_matches_helper_input_bound(example, monkeypatch, capsys):
    _, code, constructor, _ = example
    monkeypatch.setattr(sys, "stdin", io.StringIO(SECRET * 65537))
    with pytest.raises(SystemExit):
        exec(compile(code, "<returned-upload-example>", "exec"), {"__name__": "__main__"})
    constructor.assert_not_called()
    assert SECRET not in capsys.readouterr().err
