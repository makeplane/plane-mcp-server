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

from plane_mcp.tools.v2._spec import action_names

BACKTICKED = re.compile(r"`([^`]+)`")
REFERENCE = re.compile(r"^([a-z][a-z0-9_]*)(?:\s+([a-z][a-z0-9_]*))?$")


def _texts(tool):
    """Every string on a tool that a model reads."""
    yield "<description>", tool.description or ""
    for name, prop in tool.parameters["properties"].items():
        if prop.get("description"):
            yield f"param {name}", prop["description"]


def test_backticked_references_resolve(registered, resource_modules):
    """`work_item_type resolve` must name a real action on a real tool."""
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
    for tool, param in (("work_item", "pql"), ("cycle", "pql"), ("module", "pql")):
        description = registered[tool].parameters["properties"][param].get("description", "")
        assert "get_pql_reference" in description, f"{tool}.{param} does not say where to get the full PQL reference"
