"""Server-level instructions sent once to MCP clients (FastMCP `instructions` param)."""

SERVER_INSTRUCTIONS = """
## Epics

There are no epic tools — an epic is a work item whose type is named "Epic". Work
items always belong to a project; ask which if one is not named.
1. type = resolve_work_item_type(project_id, "Epic") — type.id is the type_id.
2. Create: create_work_item(project_id, type_id=type.id, name=...).
3. List: list_work_items(project_id, pql='type = "<type id>"').
4. Read / update / delete / nest: retrieve_work_item / update_work_item /
   delete_work_item by work item id (set parent=<work item id> to nest).
5. List an epic's children: list_work_items(project_id, pql='childOf("<EPIC-IDENTIFIER>")')
   using the epic's human-readable identifier (e.g. "PROJ-12") from retrieve_work_item.

## Documentation

For any how / what / why question about using or building on Plane, call
search_docs before action tools (create_*, update_*, delete_*) — those change
data, they do not explain features. Read a page in full with full_text=True,
limit=1.
"""
