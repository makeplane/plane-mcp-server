import asyncio

import pytest
from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.projects import PaginatedProjectMemberResponse, ProjectMember

from plane_mcp.tools import projects as project_tools
from plane_mcp.tools.projects import ProjectMemberInput, register_project_tools


def _tool_fn(name):
    async def get_fn():
        mcp = FastMCP("test")
        register_project_tools(mcp)
        tool = await mcp.get_tool(name)
        return tool.fn

    return asyncio.run(get_fn())


def _paginated_members(*members):
    return PaginatedProjectMemberResponse.model_validate(
        {
            "results": list(members),
            "total_count": len(members),
            "count": len(members),
            "next_cursor": "next",
            "prev_cursor": "",
            "next_page_results": False,
            "prev_page_results": False,
            "total_pages": 1,
            "total_results": len(members),
        }
    )


class FakeProjects:
    def __init__(self):
        self.calls = []
        self.member_response = _paginated_members(
            ProjectMember.model_validate(
                {
                    "id": "workspace-user-1",
                    "email": "member@example.com",
                    "display_name": "Member One",
                    "first_name": "Member",
                    "last_name": "One",
                    "avatar": "",
                    "avatar_url": "",
                }
            )
        )
        self.post_responses = [
            {"id": "project-member-1", "member": "workspace-user-1", "role": 15, "is_active": True},
            {"id": "project-member-2", "member": "workspace-user-2", "role": 20, "is_active": True},
        ]
        self.patch_response = {"id": "project-member-1", "member": "workspace-user-1", "role": 20, "is_active": True}

    def get_members_lite(self, **kwargs):
        self.calls.append(("get_members_lite", kwargs))
        return self.member_response

    def _post(self, endpoint, data):
        self.calls.append(("_post", endpoint, data))
        return self.post_responses.pop(0)

    def _patch(self, endpoint, data):
        self.calls.append(("_patch", endpoint, data))
        return self.patch_response

    def _delete(self, endpoint):
        self.calls.append(("_delete", endpoint))
        return None


@pytest.fixture
def fake_projects(monkeypatch):
    projects = FakeProjects()
    client = type("Client", (), {"projects": projects})()
    monkeypatch.setattr(project_tools, "get_plane_client_context", lambda: (client, "workspace"))
    return projects


def test_get_project_members_preserves_current_paginated_filters(fake_projects):
    fn = _tool_fn("get_project_members")

    response = fn(
        project_id="project",
        first_name="Ana",
        last_name="Ng",
        email="ana@example.com",
        display_name="Ana N",
        role_slug="member",
        is_active=True,
        is_bot=False,
        cursor="cursor",
        per_page=50,
        order_by="-created_at",
    )

    assert response.results[0].id == "workspace-user-1"
    assert "member" not in response.results[0].model_dump(exclude_none=True)
    method, kwargs = fake_projects.calls[0]
    assert method == "get_members_lite"
    assert kwargs["workspace_slug"] == "workspace"
    assert kwargs["project_id"] == "project"
    query = kwargs["params"].to_query_params()
    assert query == {
        "first_name": "Ana",
        "last_name": "Ng",
        "email": "ana@example.com",
        "display_name": "Ana N",
        "role_slug": "member",
        "is_active": "true",
        "is_bot": "false",
        "cursor": "cursor",
        "per_page": 50,
        "order_by": "-created_at",
    }


def test_get_project_members_id_is_workspace_user_not_mutation_id(fake_projects):
    list_fn = _tool_fn("get_project_members")

    listed_member = list_fn(project_id="project").results[0]

    assert listed_member.id == "workspace-user-1"
    assert fake_projects.calls == [
        (
            "get_members_lite",
            {
                "workspace_slug": "workspace",
                "project_id": "project",
                "params": fake_projects.calls[0][1]["params"],
            },
        )
    ]


def test_project_member_id_returned_by_add_is_usable_for_update(fake_projects):
    add_fn = _tool_fn("add_project_members")
    update_fn = _tool_fn("update_project_member")

    added = add_fn(project_id="project", members=[ProjectMemberInput(member="workspace-user-1", role=15)])[0]
    result = update_fn(project_id="project", project_member_id=added.id, role=20)

    assert added.id == "project-member-1"
    assert added.member == "workspace-user-1"
    assert result.id == "project-member-1"
    assert result.member == "workspace-user-1"
    assert result.role == 20
    assert fake_projects.calls[-1] == (
        "_patch",
        "workspace/projects/project/project-members/project-member-1",
        {"role": 20},
    )


def test_add_project_members_single(fake_projects):
    fn = _tool_fn("add_project_members")

    result = fn(project_id="project", members=[ProjectMemberInput(member="workspace-user-1", role=15)])

    assert result[0].id == "project-member-1"
    assert result[0].member == "workspace-user-1"
    assert (
        "_post",
        "workspace/projects/project/project-members",
        {"member": "workspace-user-1", "role": 15},
    ) in fake_projects.calls


def test_add_project_members_bulk(fake_projects):
    fn = _tool_fn("add_project_members")

    result = fn(
        project_id="project",
        members=[
            ProjectMemberInput(member="workspace-user-1", role=15),
            ProjectMemberInput(member="workspace-user-2", role=20),
        ],
    )

    assert [member.id for member in result] == ["project-member-1", "project-member-2"]
    assert (
        "_post",
        "workspace/projects/project/project-members",
        {"member": "workspace-user-1", "role": 15},
    ) in fake_projects.calls
    assert (
        "_post",
        "workspace/projects/project/project-members",
        {"member": "workspace-user-2", "role": 20},
    ) in fake_projects.calls


def test_add_project_members_rejects_empty_input_before_api_call(fake_projects):
    fn = _tool_fn("add_project_members")

    with pytest.raises(ValueError, match="at least one"):
        fn(project_id="project", members=[])

    assert fake_projects.calls == []


def test_add_project_members_rejects_duplicate_workspace_users_before_api_call(fake_projects):
    fn = _tool_fn("add_project_members")

    with pytest.raises(ValueError, match="duplicate member"):
        fn(
            project_id="project",
            members=[
                ProjectMemberInput(member="workspace-user-1", role=15),
                ProjectMemberInput(member="workspace-user-1", role=20),
            ],
        )

    assert fake_projects.calls == []


@pytest.mark.parametrize("role", [5, 15, 20])
def test_project_member_roles_accept_current_plane_values(fake_projects, role):
    fn = _tool_fn("add_project_members")

    result = fn(project_id="project", members=[ProjectMemberInput(member="workspace-user-1", role=role)])

    assert result[0].id == "project-member-1"


@pytest.mark.parametrize("tool_name", ["add_project_members", "update_project_member"])
def test_project_member_tools_reject_invalid_roles_before_api_call(fake_projects, tool_name):
    fn = _tool_fn(tool_name)

    with pytest.raises(ValueError, match="role must be one of"):
        if tool_name == "add_project_members":
            fn(
                project_id="project",
                members=[ProjectMemberInput.model_construct(member="workspace-user-1", role=99)],
            )
        else:
            fn(project_id="project", project_member_id="project-member-1", role=99)

    assert fake_projects.calls == []


def test_add_project_members_preserves_api_validation_errors(fake_projects):
    fn = _tool_fn("add_project_members")

    def fail_validation(endpoint, data):
        raise HttpError("HTTP 400: Bad Request", 400, {"member": ["Member not found in workspace"]})

    fake_projects._post = fail_validation

    with pytest.raises(HttpError) as error:
        fn(project_id="project", members=[ProjectMemberInput(member="missing-user", role=15)])

    assert error.value.status_code == 400
    assert error.value.response == {"member": ["Member not found in workspace"]}


def test_add_project_members_preserves_active_duplicate_errors(fake_projects):
    fn = _tool_fn("add_project_members")

    def fail_duplicate(endpoint, data):
        raise HttpError("HTTP 400: Bad Request", 400, {"error": "The fields member, project must make a unique set."})

    fake_projects._post = fail_duplicate

    with pytest.raises(HttpError) as error:
        fn(project_id="project", members=[ProjectMemberInput(member="workspace-user-1", role=15)])

    assert error.value.status_code == 400
    assert error.value.response == {"error": "The fields member, project must make a unique set."}


def test_add_project_members_preserves_inactive_reactivation_errors(fake_projects):
    fn = _tool_fn("add_project_members")

    def fail_inactive_duplicate(endpoint, data):
        raise HttpError("HTTP 400: Bad Request", 400, {"error": "The fields member, project must make a unique set."})

    fake_projects._post = fail_inactive_duplicate

    with pytest.raises(HttpError) as error:
        fn(project_id="project", members=[ProjectMemberInput(member="workspace-user-1", role=15)])

    assert error.value.status_code == 400
    assert error.value.response == {"error": "The fields member, project must make a unique set."}


def test_update_project_member_preserves_api_permission_errors(fake_projects):
    fn = _tool_fn("update_project_member")

    def fail_permission(endpoint, data):
        raise HttpError("HTTP 403: Forbidden", 403, {"detail": "Forbidden"})

    fake_projects._patch = fail_permission

    with pytest.raises(HttpError) as error:
        fn(project_id="project", project_member_id="project-member-1", role=20)

    assert error.value.status_code == 403
    assert error.value.response == {"detail": "Forbidden"}


def test_remove_project_member_uses_project_member_id_and_returns_none(fake_projects):
    fn = _tool_fn("remove_project_member")

    result = fn(project_id="project", project_member_id="project-member-1")

    assert result is None
    assert fake_projects.calls == [("_delete", "workspace/projects/project/project-members/project-member-1")]


def test_remove_project_member_preserves_soft_deactivation_permission_errors(fake_projects):
    fn = _tool_fn("remove_project_member")

    def fail_permission(endpoint):
        raise HttpError("HTTP 403: Forbidden", 403, {"detail": "Forbidden"})

    fake_projects._delete = fail_permission

    with pytest.raises(HttpError) as error:
        fn(project_id="project", project_member_id="project-member-1")

    assert error.value.status_code == 403
    assert error.value.response == {"detail": "Forbidden"}


def test_fastmcp_registration_and_schemas():
    async def inspect_tools():
        mcp = FastMCP("test")
        register_project_tools(mcp)
        add_tool = await mcp.get_tool("add_project_members")
        update_tool = await mcp.get_tool("update_project_member")
        remove_tool = await mcp.get_tool("remove_project_member")
        return add_tool, update_tool, remove_tool

    add_tool, update_tool, remove_tool = asyncio.run(inspect_tools())

    assert add_tool.parameters["properties"]["members"]["items"]["$ref"] == "#/$defs/ProjectMemberInput"
    assert add_tool.parameters["$defs"]["ProjectMemberInput"]["properties"]["role"]["enum"] == [5, 15, 20]
    assert "ProjectMemberMutationResponse" in add_tool.output_schema["$defs"]
    assert update_tool.parameters["properties"]["project_member_id"]["type"] == "string"
    assert update_tool.output_schema["properties"]["id"]["description"] == "Project membership record UUID."
    assert remove_tool.output_schema is None
