"""Server instructions, rendered for the surface that is actually registered.

The epic recipe has to name the tools that do each step, and those names differ
between the flat surface and the consolidated one. A single hardcoded string
named the v1 tools on both, so a v2 client read a numbered procedure citing six
tools absent from its own listing. The aliases kept the calls working, which is
what made it easy to miss.

Same split as `pql_reference`: one template, one substitution table per surface.
"""

from __future__ import annotations

TEMPLATE = """
## Epics

There are no epic tools — an epic is a work item whose type is named "Epic". Work
items always belong to a project; ask which if one is not named.
1. type = {resolve_type} with project_id and name="Epic" — type.id is the type_id.
2. Create: {create} with project_id, type_id=type.id, name=...
3. List: {list} with project_id and pql='type = "<type id>"'.
4. Read / update / delete / nest: {retrieve} / {update} / {delete} by work item
   id (set parent=<work item id> to nest).
5. List an epic's children: {list} with pql='childOf("<EPIC-IDENTIFIER>")' using
   the epic's human-readable identifier (e.g. "PROJ-12") from {retrieve}.
"""

V1_TOOLS = {
    "resolve_type": "`resolve_work_item_type`",
    "create": "`create_work_item`",
    "list": "`list_work_items`",
    "retrieve": "`retrieve_work_item`",
    "update": "`update_work_item`",
    "delete": "`delete_work_item`",
}

V2_TOOLS = {
    "resolve_type": "`workitem_type resolve`",
    "create": "`workitem create`",
    "list": "`workitem list`",
    "retrieve": "`workitem retrieve`",
    "update": "`workitem update`",
    "delete": "`workitem delete`",
}


def render(tools: dict[str, str]) -> str:
    text = TEMPLATE
    for key, name in tools.items():
        text = text.replace("{" + key + "}", name)
    return text


def instructions_for(version: str) -> str:
    return render(V1_TOOLS if version == "v1" else V2_TOOLS)
