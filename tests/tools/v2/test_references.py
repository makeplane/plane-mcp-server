"""Every tool or action a description points at must actually exist.

Descriptions are instructions the model follows literally. A reference to a tool
that was renamed away sends it to call something that is not there, and the
failure reads as the model's fault rather than ours. These names moved wholesale
in the v1 -> v2 consolidation, so the risk is concrete, not theoretical.

The convention, enforced here: write a cross-reference in backticks, as
`` `tool action` `` or `` `tool` ``. Plain prose is never parsed as a reference,
so "the workspace palette" stays prose while `` `release_label create` `` is
checked.
"""

from __future__ import annotations

import re

from plane_mcp.toolkit.spec import action_names

BACKTICKED = re.compile(r"`([^`]+)`")
REFERENCE = re.compile(r"^([a-z][a-z0-9_]*)(?:\s+([a-z][a-z0-9_]*))?$")


def _texts(tool):
    """Every string on a tool that a model reads."""
    yield "<description>", tool.description or ""
    for name, prop in tool.parameters["properties"].items():
        if prop.get("description"):
            yield f"param {name}", prop["description"]


def test_backticked_references_resolve(registered, resource_modules):
    """`workitem_type resolve` must name a real action on a real tool."""
    tools = set(registered)
    actions = {mod.NAME: set(action_names(mod.ACTIONS)) for mod in resource_modules}

    broken = []
    for name, tool in registered.items():
        for where, text in _texts(tool):
            for span in BACKTICKED.findall(text):
                match = REFERENCE.match(span.strip())
                if not match:
                    continue
                lead, follow = match.group(1), match.group(2)
                if lead not in tools:
                    continue  # a backticked field name or value, not a reference
                if follow is None or follow in actions[lead]:
                    continue
                broken.append(f"{name} :: {where} -> `{span}`: {lead} has no action {follow!r}")
    assert not broken, "descriptions name actions that do not exist:\n  " + "\n  ".join(broken)


def test_no_description_references_a_retired_tool_name(registered, legacy_tool_names, resource_modules):
    """A v1 name in an instruction is a dead pointer once it stops being advertised."""
    tools = set(registered)
    own_actions = {mod.NAME: set(action_names(mod.ACTIONS)) for mod in resource_modules}

    def strip_valid_references(text: str) -> str:
        """Remove backticked `tool action` spans; test_backticked_references_resolve owns those."""

        def drop(match):
            reference = REFERENCE.match(match.group(1).strip())
            if reference and reference.group(1) in tools:
                follow = reference.group(2)
                if follow is None or follow in actions.get(reference.group(1), set()):
                    return " "
            return match.group(0)

        return BACKTICKED.sub(drop, text)

    actions = own_actions
    dead = []
    for name, tool in registered.items():
        # An action name of this very tool is not a stale reference to a v1 tool
        # that happened to share the name.
        legal = tools | own_actions.get(name, set())
        for where, text in _texts(tool):
            for token in re.findall(r"\b[a-z][a-z0-9_]{4,}\b", strip_valid_references(text)):
                if token in legacy_tool_names and token not in legal:
                    dead.append(f"{name} :: {where} -> {token!r}")
    assert not dead, "descriptions point at tool names that are no longer advertised:\n  " + "\n  ".join(
        sorted(set(dead))
    )


def test_the_pql_hint_names_the_reference_tool(registered):
    """The hint is where a model learns PQL syntax; it must name a live tool."""
    assert "get_pql_reference" in registered
    for tool, param in (("workitem", "pql"), ("cycle", "pql"), ("module", "pql")):
        description = registered[tool].parameters["properties"][param].get("description", "")
        assert "get_pql_reference" in description, f"{tool}.{param} does not say where to get the full PQL reference"


def _resolves(reference: str, registered, actions) -> str | None:
    """None if `tool` or `tool action` names something real, else why not."""
    match = REFERENCE.match(reference.strip().strip("`"))
    if not match:
        return f"{reference!r} is not a `tool action` reference"
    tool, action = match.group(1), match.group(2)
    if tool not in registered:
        return f"`{tool}` is not an advertised tool"
    if action and action not in actions[tool]:
        return f"`{tool} {action}`: {tool} has no action {action!r}"
    return None


def test_the_server_instructions_name_live_tools(registered, resource_modules):
    """The instructions are the first thing a client reads, before any listing.

    They carry a numbered procedure, so a name that is merely *callable* is not
    enough -- a model told to use a tool it cannot see in its own listing has to
    guess. A single hardcoded string named the v1 tools on both surfaces, and the
    aliases hid it by keeping the calls working.
    """
    from plane_mcp.instructions import V2_TOOLS, instructions_for

    actions = {mod.NAME: set(action_names(mod.ACTIONS)) for mod in resource_modules}
    broken = [
        f"{key} -> {problem}"
        for key, reference in V2_TOOLS.items()
        if (problem := _resolves(reference, registered, actions))
    ]
    assert not broken, "the server instructions point at tools that do not exist:\n  " + "\n  ".join(broken)

    rendered = instructions_for("v2")
    assert "{" not in rendered, "a placeholder was left unsubstituted"


def test_the_v1_instructions_name_v1_tools(legacy_tool_names):
    """The other half of the split, which the v2 check cannot see.

    Both tables hold the same keys, so an edit to one is easy to apply to both by
    accident -- and nothing else in the suite reads the v1 spellings.
    """
    from plane_mcp.instructions import V1_TOOLS, V2_TOOLS, instructions_for

    assert set(V1_TOOLS) == set(V2_TOOLS), "the two tables must fill the same placeholders"

    wrong = [
        f"{key} -> {reference}" for key, reference in V1_TOOLS.items() if reference.strip("`") not in legacy_tool_names
    ]
    assert not wrong, "the v1 instructions name tools the flat surface does not have:\n  " + "\n  ".join(wrong)

    assert "{" not in instructions_for("v1")


def test_the_pql_reference_resolvers_name_live_tools(registered, resource_modules):
    """The PQL reference tells a model which tool resolves a name to an id.

    It reaches the model as tool *output* rather than as a description, so the
    checks above never saw it -- and the v2 table went on naming `work_item_type
    list` after that tool became `workitem_type`. Every pointer is followed here.
    """
    from plane_mcp.pql_reference import V2_RESOLVERS

    actions = {mod.NAME: set(action_names(mod.ACTIONS)) for mod in resource_modules}
    broken = [
        f"{key} -> {problem}"
        for key, reference in V2_RESOLVERS.items()
        if (problem := _resolves(reference, registered, actions))
    ]
    assert not broken, "the PQL reference points at tools that do not exist:\n  " + "\n  ".join(broken)
