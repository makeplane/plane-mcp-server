"""Project-membership management through the consolidated member tool."""

from plane.models.projects import ProjectMember


def test_project_member_listing_uses_v1_project_members(registered, spy):
    spy.returns["projects.get_members"] = [
        ProjectMember(id="user-1", first_name="Ada", last_name="Lovelace", role=15)
    ]

    result = registered["member"].fn(action="list_project", project_id="project-1")

    assert [(row.id, row.role) for row in result] == [("user-1", 15)]
    call = spy.recorder.only()
    assert call.method == "projects.get_members"
    assert call.kwargs == {"workspace_slug": "acme", "project_id": "project-1"}


def test_project_member_can_be_added_by_workspace_user_id(registered, spy):
    spy.returns["projects._post"] = {
        "id": "membership-1",
        "member": "user-1",
        "role": 15,
    }

    result = registered["member"].fn(
        action="add_project",
        project_id="project-1",
        member_id="user-1",
        role="member",
    )

    assert result["id"] == "membership-1"
    call = spy.recorder.only()
    assert call.method == "projects._post"
    assert call.kwargs == {
        "endpoint": "acme/projects/project-1/members",
        "data": {"member": "user-1", "role": 15},
    }


def test_project_member_contributor_alias_maps_to_v1_member_role(registered, spy):
    spy.returns["projects._post"] = {
        "id": "membership-1",
        "member": "user-1",
        "role": 15,
    }

    registered["member"].fn(
        action="add_project",
        project_id="project-1",
        member_id="user-1",
        role="contributor",
    )

    call = spy.recorder.only()
    assert call.kwargs["data"]["role"] == 15


def test_project_member_role_can_be_changed_by_membership_id(registered, spy):
    spy.returns["projects._patch"] = {
        "id": "membership-1",
        "member": "user-1",
        "role": 20,
    }

    result = registered["member"].fn(
        action="update_project",
        project_id="project-1",
        membership_id="membership-1",
        role="admin",
    )

    assert result["role"] == 20
    call = spy.recorder.only()
    assert call.method == "projects._patch"
    assert call.kwargs == {
        "endpoint": "acme/projects/project-1/members/membership-1",
        "data": {"role": 20},
    }


def test_project_member_can_be_removed_without_removing_workspace_user(registered, spy):
    result = registered["member"].fn(
        action="remove_project",
        project_id="project-1",
        membership_id="membership-1",
    )

    assert result is None
    call = spy.recorder.only()
    assert call.method == "projects._delete"
    assert call.kwargs == {
        "endpoint": "acme/projects/project-1/members/membership-1",
    }


def test_project_member_rejects_unknown_role(registered, spy):
    result = registered["member"].fn(
        action="add_project",
        project_id="project-1",
        member_id="user-1",
        role="commenter",
    )

    assert result == "Error: role must be guest, member, contributor, admin, 5, 15, or 20."
    assert spy.recorder.calls == []
