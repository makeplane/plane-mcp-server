"""Every action of every resource, executed against a validating stand-in client.

This is the test that would have caught the two defects the prototype shipped:
a flat payload where the SDK wanted a nested model, and a dict where it wanted a
Pydantic params object. Both bind cleanly against a plain mock and only fail
against a real workspace. `SpyClient` binds against the genuine SDK signature
and type-checks each argument, so they fail here instead.

It also pins the guard clauses: an action invoked with nothing must name what it
needs rather than reaching the network.
"""

from __future__ import annotations

import inspect

import pytest

from plane_mcp.toolkit.spec import action_names

# Values that satisfy a parameter well enough to reach the SDK call. Enum-valued
# parameters need a member of their own vocabulary; the rest take a plausible id.
SAMPLES: dict[str, object] = {
    "status": {"cycle": "current", "module": "backlog", "intake": 1, "release": "planned"},
    "priority": "medium",
    "group": "started",
    "relation_type": "blocked_by",
    "property_type": "TEXT",
    "access": 1,
    "network": 2,
    "timezone": "UTC",
    "work_item_identifier": "ENG-42",
}

# Actions that require *one of* several optional parameters -- a condition the
# declaration cannot express, so the case is spelled out here.
CONDITIONAL: dict[tuple[str, str], dict[str, object]] = {
    ("cycle", "manage_work_items"): {"add_ids": "id-1"},
    ("module", "manage_work_items"): {"add_ids": "id-1"},
    ("milestone", "manage_work_items"): {"add_ids": "id-1"},
    ("work_item", "manage_assignee"): {"add_user_id": "id-1"},
    ("work_item", "manage_label"): {"add_label_id": "id-1"},
    ("work_item", "count"): {"pql": "state__group = 'started'"},
    ("intake", "update"): {"status": 1},
    ("work_item_relation", "create"): {"relation_type": "blocked_by"},
    ("work_item_property", "manage_type_properties"): {"attach_ids": "id-1"},
    ("project_estimate", "create_points"): {"points": '[{"value": "1", "key": 0}]'},
    ("customer", "delete"): {"customer_id": "id-1"},
    ("customer", "manage_work_items"): {"link_ids": "id-1"},
    ("customer_property", "set_values"): {"values": '{"prop-1": ["Enterprise"]}'},
    ("release", "update_changelog"): {"description_html": "<p>notes</p>"},
    ("release", "manage_work_items"): {"add_ids": "id-1"},
    ("release_label", "list"): {},
}

# Actions whose guard clause legitimately answers without calling the SDK.
NO_CALL_EXPECTED: set[tuple[str, str]] = {
    ("get_pql_reference", "read"),  # returns static reference text
}

# Actions that need populated remote state or an outbound HTTP fetch to get past
# their own preconditions. Covered by tests/tools/v2/test_attachments.py.
NEEDS_FIXTURE: set[tuple[str, str]] = {
    ("work_item_attachment", "read"),
    ("work_item_attachment", "download_url"),
    ("work_item_attachment", "upload_from_url"),
}


def _value(mod_name: str, param: str, annotation: type) -> object:
    sample = SAMPLES.get(param)
    if isinstance(sample, dict):
        sample = sample.get(mod_name)
    if sample is not None:
        return sample
    if annotation is bool or annotation == "bool":
        return True
    if annotation is int or annotation == "int":
        return 3
    if annotation is float or annotation == "float":
        return 1.0
    return f"{param}-value"


def _call_args(mod, action, tool) -> dict[str, object]:
    signature = inspect.signature(tool.fn)
    args: dict[str, object] = {}
    if "action" in signature.parameters:
        args["action"] = action.name
    for param in action.requires:
        annotation = signature.parameters[param].annotation
        args[param] = _value(mod.NAME, param, annotation)
    args.update(CONDITIONAL.get((mod.NAME, action.name), {}))
    return args


def _cases(mods):
    for mod in mods:
        for action in mod.ACTIONS:
            yield pytest.param(mod, action, id=f"{mod.NAME}.{action.name}")


def pytest_generate_tests(metafunc):
    if {"mod", "action"} <= set(metafunc.fixturenames):
        from plane_mcp.tools.v2.registry import RESOURCES

        metafunc.parametrize(("mod", "action"), list(_cases(RESOURCES)))


def test_action_reaches_the_sdk(mod, action, registered, spy):
    """A fully-specified action must produce a real, well-typed SDK call."""
    if (mod.NAME, action.name) in NEEDS_FIXTURE:
        pytest.skip("needs populated remote state; covered by test_attachments.py")
    tool = registered[mod.NAME]
    result = tool.fn(**_call_args(mod, action, tool))

    assert not (isinstance(result, str) and result.startswith("Error:")), (
        f"{mod.NAME}.{action.name} rejected its own declared required params: {result}"
    )
    if (mod.NAME, action.name) in NO_CALL_EXPECTED:
        return
    assert spy.recorder.calls, f"{mod.NAME}.{action.name} made no SDK call"


def test_missing_required_params_are_reported(mod, action, registered, spy):
    """Called bare, an action names what it needs instead of hitting the API."""
    if not action.requires:
        pytest.skip("action has no required parameters")
    tool = registered[mod.NAME]
    signature = inspect.signature(tool.fn)
    args = {"action": action.name} if "action" in signature.parameters else {}

    result = tool.fn(**args)

    assert isinstance(result, str) and result.startswith("Error:"), (
        f"{mod.NAME}.{action.name} accepted a call with none of {action.requires} supplied"
    )
    assert not spy.recorder.calls, f"{mod.NAME}.{action.name} called {spy.recorder.methods} despite missing params"


# Parameters where 0 is a real value, not "unset". Passing one through the
# sentinel helper silently drops it and the write looks like it succeeded.
MEANINGFUL_ZEROS = [
    ("project", dict(action="update", project_id="p", network=0), "network"),
    ("label", dict(action="update", project_id="p", label_id="l", sort_order=0), "sort_order"),
    ("state", dict(action="update", project_id="p", state_id="s", sequence=0), "sequence"),
    (
        "project_estimate",
        dict(action="update_point", project_id="p", estimate_id="e", estimate_point_id="pt", key=0),
        "key",
    ),
    ("release_label", dict(action="update", label_id="l", sort_order=0), "sort_order"),
]


@pytest.mark.parametrize(
    ("tool_name", "args", "field"), MEANINGFUL_ZEROS, ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_zero_that_means_something_reaches_the_sdk(tool_name, args, field, registered, spy):
    registered[tool_name].fn(**args)

    sent = getattr(spy.recorder.only().kwargs["data"], field)
    assert sent == 0, f"{tool_name}.{args['action']} dropped {field}=0; it was sent as {sent!r}"


def test_action_literal_covers_every_declared_action(mod, action, registered):
    tool = registered[mod.NAME]
    enum = tool.parameters["properties"].get("action", {}).get("enum")
    if enum is None:
        assert len(mod.ACTIONS) == 1
        return
    assert action.name in enum
    assert tuple(enum) == action_names(mod.ACTIONS)
