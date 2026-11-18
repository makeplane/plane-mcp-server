"""The role tool reaches the v2 role and permission-scheme endpoints with the arguments it was given."""


def test_delete_passes_reassign_to(registered, spy):
    registered["role"].fn(action="delete", role_id="role-1", reassign_to="role-2")

    call = spy.recorder.only()
    assert call.method == "v2.workspaces.roles.delete"
    assert call.kwargs == {"slug": "acme", "role": "role-1", "reassign_to": "role-2"}


def test_disabling_a_role_sends_status_and_reassign_to(registered, spy):
    registered["role"].fn(action="update", role_id="role-1", status="inactive", reassign_to="role-2")

    call = spy.recorder.only()
    assert call.method == "v2.workspaces.roles.update"
    assert call.kwargs["data"].model_dump(exclude_none=True) == {"status": "inactive", "reassign_to": "role-2"}


def test_an_empty_scheme_list_clears_a_role_but_none_leaves_it(registered, spy):
    registered["role"].fn(action="update", role_id="role-1", permission_scheme_ids=[])
    registered["role"].fn(action="update", role_id="role-1", name="Renamed")

    cleared, renamed = (call.kwargs["data"].model_dump(exclude_none=True) for call in spy.recorder.calls)
    assert cleared == {"permission_scheme_ids": []}
    assert renamed == {"name": "Renamed"}


def test_the_catalogue_is_read_for_the_namespace(registered, spy):
    registered["role"].fn(action="list_permissions", namespace="project")

    call = spy.recorder.only()
    assert call.method == "v2.workspaces.permissions.resources"
    assert call.kwargs == {"slug": "acme", "namespace": "project"}


def test_an_unknown_namespace_is_refused_before_any_request(registered, spy):
    result = registered["role"].fn(action="create", name="Triager", namespace="team")

    assert result.startswith("Error: namespace must be one of: workspace, project")
    assert spy.recorder.calls == []


def test_listings_ask_for_and_return_summary_rows_only(registered, spy):
    from plane.models.v2 import PermissionScheme, Role

    spy.returns["v2.workspaces.roles.iterate"] = [Role(id="r1", name="Triager", permissions=["workitem:view"])]
    spy.returns["v2.workspaces.permission_schemes.iterate"] = [PermissionScheme(id="s1", permissions=["x:view"])]

    roles = registered["role"].fn(action="list")
    schemes = registered["role"].fn(action="list_schemes")

    role_call, scheme_call = spy.recorder.calls
    assert role_call.kwargs["fields"] == ("id", "name", "slug", "namespace", "is_system", "status")
    assert scheme_call.kwargs["fields"] == ("id", "name", "slug", "namespace", "is_system")
    assert "permissions" not in roles[0] and roles[0]["name"] == "Triager"
    assert "permissions" not in schemes[0]
