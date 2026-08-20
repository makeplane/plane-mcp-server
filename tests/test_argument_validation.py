"""Refusing arguments the chosen action has no use for.

One flat schema per tool cannot express which action wants what, so an argument
meant for a different action validates cleanly and is then silently dropped. The
observed failure: `workitem count` asked with a project id and a filter under the
wrong parameter name answered for the whole workspace, in the shape of a correct
answer to the question that was asked.

Two halves: the rule, exercised directly, and the middleware through a real
server -- the ordering against schema validation and against the retired-name
transform are FastMCP details, so they are tested rather than assumed.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from plane_mcp.middleware import ValidateActionArguments
from plane_mcp.tools.registry import action_arguments

os.environ.setdefault("PLANE_API_KEY", "test")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "test")


@pytest.fixture(scope="module")
def rejection():
    """The rejection message for a call, or None when the call is acceptable."""
    return ValidateActionArguments().rejection


# --- the rule ----------------------------------------------------------------


def test_an_argument_the_action_does_not_take_is_named(rejection):
    message = rejection("workitem", {"action": "count", "query": "urgent"})
    assert message and "does not take: query" in message


def test_the_rejection_lists_what_the_action_does_take(rejection):
    """A refusal that does not say what to send instead just costs another turn."""
    message = rejection("workitem", {"action": "count", "query": "urgent"})
    for accepted in action_arguments()["workitem"]["count"]:
        assert accepted in message


def test_every_stray_argument_is_named_at_once(rejection):
    """Naming one at a time would cost a round trip per mistake."""
    message = rejection("workitem", {"action": "count", "query": "x", "name": "y"})
    assert message and "name, query" in message


def test_an_accepted_argument_passes(rejection):
    assert rejection("workitem", {"action": "count", "pql": 'priority = "urgent"'}) is None


def test_project_id_is_accepted_on_count(rejection):
    """count scopes to a project, so the parameter that surfaced the bug is now valid."""
    assert rejection("workitem", {"action": "count", "project_id": "p"}) is None


@pytest.mark.parametrize("value", ["", 0, None, [], {}, False], ids=["str", "int", "none", "list", "dict", "bool"])
def test_an_argument_left_at_its_default_says_nothing_and_is_allowed(rejection, value):
    """Some clients pad a request with every parameter; that is not an instruction."""
    assert rejection("workitem", {"action": "count", "query": value}) is None


def test_action_itself_is_never_stray(rejection):
    assert rejection("workitem", {"action": "count"}) is None


def test_a_call_that_chose_no_action_is_told_which_actions_exist(rejection):
    """The observed failure: three of one weak model's six errored calls omitted
    `action`, and Pydantic's missing_argument answer names the parameter without
    naming a single permitted value -- so the turn buys nothing."""
    message = rejection("project", {"project_id": "p"})
    assert message and "requires an action" in message
    for action in action_arguments()["project"]:
        assert action in message, f"{action} missing from the refusal"


def test_every_resource_names_its_actions_when_none_is_chosen(rejection):
    """A resource left out would answer the one question the caller has with silence."""
    for tool, actions in action_arguments().items():
        message = rejection(tool, {})
        assert message, f"{tool} refused a call with no action without saying why"
        for action in actions:
            assert action in message, f"{tool} omitted {action}"


def test_a_call_with_no_action_on_an_unknown_tool_is_left_to_the_server(rejection):
    """The missing-action check must not claim tools this server does not own."""
    assert rejection("not_a_tool", {}) is None


def test_an_unknown_action_is_left_to_the_schema(rejection):
    """The Literal reports the permitted set; a second opinion here would only muddle it."""
    assert rejection("workitem", {"action": "cout", "query": "x"}) is None


def test_an_unknown_tool_is_left_to_the_server(rejection):
    assert rejection("not_a_tool", {"action": "count", "query": "x"}) is None


def test_a_retired_name_is_not_checked(rejection):
    """It arrives with no action and under its own parameter spelling."""
    assert rejection("retrieve_work_item", {"work_item_id": "w"}) is None


def test_every_action_of_every_resource_accepts_its_own_declaration(rejection):
    """Whatever an action declares must pass -- a table that rejects it is inverted."""
    for tool, actions in action_arguments().items():
        for action, accepted in actions.items():
            arguments = {"action": action, **dict.fromkeys(accepted, "x")}
            assert rejection(tool, arguments) is None, f"{tool} {action} rejected its own parameters"


# --- the middleware, through a real server -----------------------------------


def _call(tool: str, arguments: dict) -> str:
    """Call `tool` on a real server and return the text the caller receives."""
    from fastmcp import Client

    import plane_mcp.server as server_module

    async def run():
        async with Client(server_module.get_stdio_mcp()) as client:
            result = await client.call_tool(tool, arguments)
            return str(result.content[0].text if result.content else "")

    try:
        return asyncio.new_event_loop().run_until_complete(run())
    except Exception as exc:  # noqa: BLE001 - the error text is the assertion
        return str(exc)


def test_a_stray_argument_never_reaches_plane():
    """The 403 the credentials would earn is the proof a call got through."""
    answer = _call("workitem", {"action": "count", "project_id": "p", "query": "urgent"})
    assert "does not take: query" in answer
    assert "403" not in answer


def test_a_call_with_no_action_never_reaches_plane():
    """The refusal has to replace the schema error, not arrive after a wasted call."""
    answer = _call("workitem", {"project_id": "p"})
    assert "requires an action" in answer
    assert "403" not in answer


def test_a_clean_call_is_not_blocked():
    answer = _call("workitem", {"action": "count", "project_id": "p", "pql": 'priority = "urgent"'})
    assert "does not take" not in answer
    assert "403" in answer, "the call did not reach Plane"


def test_a_retired_name_still_works():
    """169 aliases arrive under retired spellings; checking them would break every one."""
    answer = _call("retrieve_work_item", {"project_id": "p", "work_item_id": "w"})
    assert "does not take" not in answer


def test_every_transport_gets_the_check():
    """A transport left behind would validate on stdio and silently drop on HTTP."""
    import plane_mcp.server as server_module

    for factory in (server_module.get_stdio_mcp, server_module.get_header_mcp):
        stack = [type(m).__name__ for m in factory().middleware]
        assert "ValidateActionArguments" in stack, f"{factory.__name__} is missing the check"
