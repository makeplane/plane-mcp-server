"""Upload lifecycle contracts, validation, scope, and safe failures."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastmcp.server.middleware import MiddlewareContext
from plane import PlaneClient
from plane.errors.errors import HttpError

from plane_mcp.attachment_upload import begin_upload, complete_upload, validate_metadata
from plane_mcp.attachments import UPLOAD_SIZE_LIMIT
from plane_mcp.middleware import PlaneLoggingMiddleware, ValidateActionArguments
from plane_mcp.tools import workitem_attachment as module

PROJECT = "11111111-1111-4111-8111-111111111111"
WORK_ITEM = "22222222-2222-4222-8222-222222222222"
ATTACHMENT = "33333333-3333-4333-8333-333333333333"
PATH = f"acme/projects/{PROJECT}/work-items/{WORK_ITEM}/attachments"
SECRET = "signed-secret-value"
UPLOAD = {"url": f"https://storage.example/upload?signature={SECRET}", "fields": {"policy": SECRET}}
METADATA = {"name": "shot.png", "content_type": "image/png", "size": 42}


@pytest.fixture
def lifecycle(registered, spy):
    spy.returns["work_items.attachments._post"] = {"asset_id": ATTACHMENT, "upload_data": UPLOAD}
    spy.returns["work_items.attachments._patch"] = None
    return registered[module.NAME].fn, spy


def test_begin_returns_signed_instructions_without_completing(lifecycle):
    tool, spy = lifecycle
    result = tool(action="begin_upload", project_id=PROJECT, workitem_id=WORK_ITEM, **METADATA)
    assert result == {"attachment_id": ATTACHMENT, "upload_data": UPLOAD, **METADATA}
    call = spy.recorder.only()
    assert call.method == "work_items.attachments._post"
    assert call.kwargs == {"endpoint": PATH, "data": {"name": "shot.png", "type": "image/png", "size": 42}}


def test_complete_uses_scoped_patch_and_accepts_204(lifecycle):
    tool, spy = lifecycle
    result = tool(
        action="complete_upload",
        project_id=PROJECT,
        workitem_id=WORK_ITEM,
        attachment_id=ATTACHMENT,
        transfer_succeeded=True,
    )
    assert result == {"attachment_id": ATTACHMENT, "completion_confirmed": True}
    assert spy.recorder.only().kwargs == {
        "endpoint": f"{PATH}/{ATTACHMENT}",
        "data": {"is_uploaded": True},
    }


@pytest.mark.parametrize("transfer_succeeded", [False, None, "true", 1])
def test_completion_refuses_unconfirmed_transfer(lifecycle, transfer_succeeded):
    tool, spy = lifecycle
    try:
        result = tool(
            action="complete_upload",
            project_id=PROJECT,
            workitem_id=WORK_ITEM,
            attachment_id=ATTACHMENT,
            transfer_succeeded=transfer_succeeded,
        )
    except ValueError:
        pass
    else:
        assert result.startswith("Error:")
    assert not spy.recorder.calls


@pytest.mark.parametrize("field", ["name", "content_type", "size", "project_id", "workitem_id"])
def test_begin_names_only_missing_argument(lifecycle, field):
    tool, spy = lifecycle
    arguments = {"project_id": PROJECT, "workitem_id": WORK_ITEM, **METADATA}
    del arguments[field]
    assert tool(action="begin_upload", **arguments).endswith(f"requires: {field}.")
    assert not spy.recorder.calls


@pytest.mark.parametrize(
    "change",
    [
        {"size": -1},
        {"size": UPLOAD_SIZE_LIMIT + 1},
        {"size": True},
        {"size": 1.5},
        {"name": "../shot.png"},
        {"name": "C:\\shot.png"},
        {"name": "\rshot.png"},
        {"name": " "},
        {"name": "a" * 256},
        {"name": "é" * 128},
        {"name": ".."},
        {"content_type": "png"},
        {"content_type": "image/png\r\nX: y"},
        {"content_type": "image/png; charset=utf-8"},
    ],
)
def test_invalid_metadata_never_calls_api(lifecycle, change):
    tool, spy = lifecycle
    with pytest.raises(ValueError):
        tool(action="begin_upload", project_id=PROJECT, workitem_id=WORK_ITEM, **(METADATA | change))
    assert not spy.recorder.calls


def test_size_boundaries():
    for size in (1, UPLOAD_SIZE_LIMIT):
        validate_metadata("file.bin", "application/octet-stream", size)
    with pytest.raises(ValueError):
        validate_metadata("empty", "text/plain", 0)


@pytest.mark.parametrize("action", ["begin_upload", "complete_upload"])
@pytest.mark.parametrize("status", [401, 403, 404])
def test_authorization_errors_are_not_bypassed_or_exposed(lifecycle, action, status):
    tool, spy = lifecycle
    method = "_post" if action == "begin_upload" else "_patch"
    spy.returns[f"work_items.attachments.{method}"] = HttpError(SECRET, status, {"upload_data": UPLOAD})
    arguments = METADATA if action == "begin_upload" else {"attachment_id": ATTACHMENT, "transfer_succeeded": True}
    with pytest.raises(ValueError, match=f"HTTP {status}") as error:
        tool(action=action, project_id=PROJECT, workitem_id=WORK_ITEM, **arguments)
    assert SECRET not in str(error.value)
    assert spy.recorder.only().kwargs["endpoint"].startswith(PATH)


@pytest.mark.parametrize("action", ["begin_upload", "complete_upload"])
def test_timeout_reports_uncertainty_without_sensitive_details(lifecycle, action):
    tool, spy = lifecycle
    method = "_post" if action == "begin_upload" else "_patch"
    spy.returns[f"work_items.attachments.{method}"] = requests.Timeout(SECRET)
    arguments = METADATA if action == "begin_upload" else {"attachment_id": ATTACHMENT, "transfer_succeeded": True}
    with pytest.raises(ValueError, match="uncertain") as error:
        tool(action=action, project_id=PROJECT, workitem_id=WORK_ITEM, **arguments)
    assert SECRET not in str(error.value)


def test_new_actions_reject_scope_path_injection(lifecycle):
    tool, spy = lifecycle
    with pytest.raises(ValueError, match="UUID"):
        tool(action="begin_upload", project_id="../another-project", workitem_id=WORK_ITEM, **METADATA)
    assert not spy.recorder.calls


def test_schemas_and_cross_action_arguments(registered):
    properties = registered[module.NAME].parameters["properties"]
    assert properties["size"]["type"] == "integer"
    assert properties["transfer_succeeded"]["type"] == "boolean"
    assert "file_path" not in properties and "file_bytes" not in properties
    validator = ValidateActionArguments()
    assert validator.rejection(module.NAME, {"action": "begin_upload", **METADATA}) is None
    assert "does not take" in validator.rejection(module.NAME, {"action": "list", "size": 42})
    assert "does not take" in validator.rejection(module.NAME, {"action": "complete_upload", "name": "x"})


@pytest.mark.parametrize("api_key,access_token", [("plane-api-secret", None), (None, "oauth-secret")])
def test_real_sdk_preserves_connection_auth_and_endpoint(api_key, access_token):
    client = PlaneClient(base_url="https://plane.example", api_key=api_key, access_token=access_token)
    resource = client.work_items.attachments
    created = requests.Response()
    created.status_code = 200
    created.headers["Content-Type"] = "application/json"
    import json

    created._content = json.dumps({"asset_id": ATTACHMENT, "upload_data": UPLOAD}).encode()
    confirmed = requests.Response()
    confirmed.status_code = 204
    resource.session.post = Mock(return_value=created)
    resource.session.patch = Mock(return_value=confirmed)
    begin_upload(resource, "acme", PROJECT, WORK_ITEM, **METADATA)
    complete_upload(resource, "acme", PROJECT, WORK_ITEM, ATTACHMENT)
    expected_auth = {"X-Api-Key": api_key} if api_key else {"Authorization": f"Bearer {access_token}"}
    for call in (resource.session.post.call_args, resource.session.patch.call_args):
        assert call.args[0].startswith(f"https://plane.example/api/v1/workspaces/{PATH}")
        assert call.kwargs["headers"] == {"Content-Type": "application/json", **expected_auth}
    assert resource.session.patch.call_args.kwargs["json"] == {"is_uploaded": True}


@pytest.mark.parametrize(
    "raw", [None, {}, {"asset_id": ATTACHMENT, "upload_data": {"url": SECRET, "fields": [SECRET]}}]
)
def test_malformed_plane_instructions_are_safe(lifecycle, raw):
    tool, spy = lifecycle
    spy.returns["work_items.attachments._post"] = raw
    with pytest.raises(ValueError, match="uncertain") as error:
        tool(action="begin_upload", project_id=PROJECT, workitem_id=WORK_ITEM, **METADATA)
    assert SECRET not in str(error.value)
    assert "_patch" not in str(spy.recorder.methods)


@pytest.mark.parametrize("name", [module.NAME, "upload_work_item_attachment_from_url"])
def test_attachment_logs_omit_payloads_and_signed_errors(name):
    middleware = PlaneLoggingMiddleware(include_payloads=True, include_payload_length=True)
    context = MiddlewareContext(
        message=SimpleNamespace(name=name, arguments={"action": "begin_upload", "upload_data": UPLOAD}),
        method="tools/call",
    )
    before = middleware._create_before_message(context)
    after = middleware._create_after_message(context, 0)
    error = middleware._create_error_message(context, 0, ValueError(str(UPLOAD)))
    assert before["resource"] == module.NAME
    assert "payload" not in before
    assert SECRET not in str([before, after, error])


@pytest.mark.parametrize("argument,value", [("size", True), ("size", 1.5), ("transfer_succeeded", "true")])
def test_mcp_schema_rejects_wrong_argument_types(lifecycle, argument, value):
    import asyncio

    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError

    _, spy = lifecycle
    server = FastMCP("upload-schema")
    module.register(server)
    arguments = {"action": "begin_upload", "project_id": PROJECT, "workitem_id": WORK_ITEM, **METADATA}
    if argument == "transfer_succeeded":
        arguments = {
            "action": "complete_upload",
            "project_id": PROJECT,
            "workitem_id": WORK_ITEM,
            "attachment_id": ATTACHMENT,
        }
    arguments[argument] = value

    async def run():
        async with Client(server) as client:
            with pytest.raises(ToolError):
                await client.call_tool(module.NAME, arguments)

    asyncio.run(run())
    assert not spy.recorder.calls


@pytest.mark.parametrize(
    "raw",
    [
        {"asset_id": 1, "upload_data": UPLOAD},
        {"asset_id": ATTACHMENT, "upload_data": {"url": "http://storage.example/", "fields": {"policy": SECRET}}},
        {"asset_id": ATTACHMENT, "upload_data": {"url": "https://storage.example/", "fields": {}}},
    ],
)
def test_invalid_envelope_is_safe_and_never_completes(lifecycle, raw):
    tool, spy = lifecycle
    spy.returns["work_items.attachments._post"] = raw
    with pytest.raises(ValueError, match="uncertain") as error:
        tool(action="begin_upload", project_id=PROJECT, workitem_id=WORK_ITEM, **METADATA)
    assert SECRET not in str(error.value)
    assert spy.recorder.methods == ["work_items.attachments._post"]


def test_scope_components_are_encoded_and_ids_are_canonical(lifecycle, monkeypatch):
    tool, spy = lifecycle
    monkeypatch.setattr(module, "get_plane_client_context", lambda: (spy, "team%2fother"))
    tool(action="begin_upload", project_id=f"urn:uuid:{PROJECT}", workitem_id=WORK_ITEM, **METADATA)
    assert spy.recorder.only().kwargs["endpoint"] == PATH.replace("acme/", "team%252fother/")


def test_invalid_unicode_metadata_is_safe(lifecycle):
    tool, spy = lifecycle
    with pytest.raises(ValueError, match="UTF-8"):
        tool(action="begin_upload", project_id=PROJECT, workitem_id=WORK_ITEM, **(METADATA | {"name": "\ud800"}))
    assert not spy.recorder.calls


@pytest.mark.parametrize("action", ["list", "delete", "download_url", "read"])
def test_existing_attachment_errors_do_not_expose_signed_response(lifecycle, action):
    tool, spy = lifecycle
    method = "delete" if action == "delete" else "list"
    spy.returns[f"work_items.attachments.{method}"] = HttpError(SECRET, 403, {"url": UPLOAD["url"]})
    with pytest.raises(ValueError, match="HTTP 403") as error:
        tool(action=action, project_id=PROJECT, workitem_id=WORK_ITEM, attachment_id=ATTACHMENT)
    assert SECRET not in str(error.value)


def test_invalid_attachment_action_is_not_logged():
    middleware = PlaneLoggingMiddleware(include_payloads=True)
    context = MiddlewareContext(
        message=SimpleNamespace(name=module.NAME, arguments={"action": UPLOAD["url"]}), method="tools/call"
    )
    assert SECRET not in str(middleware._create_before_message(context))
    assert SECRET not in str(middleware._create_error_message(context, 0, ValueError(SECRET)))


@pytest.mark.parametrize("connection", ["stdio", "header", "oauth"])
@pytest.mark.parametrize("action", ["begin_upload", "complete_upload"])
def test_actions_use_current_auth_context_instead_of_environment(connection, action, registered, monkeypatch):
    import json

    from fastmcp.server.auth.auth import AccessToken

    import plane_mcp.client as client_module

    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example")
    monkeypatch.delenv("PLANE_INTERNAL_BASE_URL", raising=False)
    monkeypatch.setenv("PLANE_API_KEY", "environment-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "environment-workspace")
    token = (
        None
        if connection == "stdio"
        else AccessToken(
            token="connection-token",
            client_id="test",
            scopes=[],
            claims={
                "workspace_slug": "connection-workspace",
                "auth_method": "api_key_header" if connection == "header" else "oauth",
            },
        )
    )
    monkeypatch.setattr(client_module, "get_access_token", lambda: token)
    monkeypatch.setattr(module, "get_plane_client_context", client_module.get_plane_client_context)
    calls = []

    def response(session, url, **kwargs):
        calls.append((url, kwargs))
        result = requests.Response()
        result.status_code = 200 if action == "begin_upload" else 204
        result.headers["Content-Type"] = "application/json"
        result._content = json.dumps({"asset_id": ATTACHMENT, "upload_data": UPLOAD}).encode()
        return result

    monkeypatch.setattr(requests.Session, "post" if action == "begin_upload" else "patch", response)
    arguments = METADATA if action == "begin_upload" else {"attachment_id": ATTACHMENT, "transfer_succeeded": True}
    registered[module.NAME].fn(action=action, project_id=PROJECT, workitem_id=WORK_ITEM, **arguments)
    assert len(calls) == 1
    url, kwargs = calls[0]
    workspace = "environment-workspace" if connection == "stdio" else "connection-workspace"
    assert url.startswith(f"https://plane.example/api/v1/workspaces/{workspace}/projects/{PROJECT}/")
    expected = (
        {"Authorization": "Bearer connection-token"}
        if connection == "oauth"
        else {"X-Api-Key": "environment-key" if connection == "stdio" else "connection-token"}
    )
    assert kwargs["headers"] == {"Content-Type": "application/json", **expected}
