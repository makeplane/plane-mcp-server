"""Server-level instructions sent once to MCP clients (FastMCP `instructions` param)."""

WORK_ITEM_TYPE_SCOPING_INSTRUCTIONS = """
## Work item type scoping

To get a usable work item type for a project (e.g. "Epic", "Initiative"), call resolve_work_item_type(project_id, name). It returns the type (its id is the type_id for create_work_item) and handles everything in one step:
- If the workspace owns work item types, it finds or creates the type at the workspace level and imports it into the project (project-level creation is not allowed in this mode).
- Otherwise it finds or creates the type at the project level, enabling the project's work item types feature first if needed.

Prefer this single tool over manually combining get_workspace_features, list_work_item_types, create_work_item_type, and import_work_item_types_to_project — it does all of that deterministically and never creates a duplicate.
"""

EPIC_INSTRUCTIONS = """
## Epics

This server has no dedicated epic tools (no create_epic, list_epics, retrieve_epic, update_epic, delete_epic, list_epic_issues, add_epic_issues). An "epic" is just a work item whose work item type is named "Epic".

1. type = resolve_work_item_type(project_id, "Epic") — see "Work item type scoping".
2. Create: create_work_item(project_id=project_id, type_id=type.id, name=<epic name>).
3. List epics: list_work_items(project_id=project_id, pql='type = "<type id>"') (or pql='isEpic()').
4. Read, update, or delete: retrieve_work_item / update_work_item / delete_work_item, using the epic's work item id.
5. Nest a work item under an epic: create_work_item or update_work_item with parent=<epic work item id>.
6. List an epic's children: list_work_items(project_id=project_id, pql='childOf("<EPIC-IDENTIFIER>")'), where <EPIC-IDENTIFIER> is the epic's human-readable identifier (e.g. "PROJ-12") from retrieve_work_item.
"""

INITIATIVE_INSTRUCTIONS = """
## Initiatives

Call get_workspace_features() first. Pick exactly one path — never mix them.

If initiatives is true — native workspace-level objects (no project_id needed):
- Create: create_initiative(name=...).
- List: list_initiatives().
- Read/update/delete: retrieve_initiative / update_initiative / delete_initiative by initiative id.

If initiatives is false — fall back to an "Initiative" work item type inside a project:
1. If the user has not named a project, ask which project to use before proceeding.
2. type = resolve_work_item_type(project_id, "Initiative") — handles everything: checks if the type is already in the project, finds or creates it at the workspace level if workspace owns types (the common case — "Initiative" is normally a workspace-level type imported into projects), or creates it at the project level if the project owns its own types. Never creates a duplicate.
3. Create: create_work_item(project_id=project_id, type_id=type.id, name=<initiative name>).
4. List: list_work_items(project_id=project_id, pql='type = "<type id>"').
5. Read/update/delete: retrieve_work_item / update_work_item / delete_work_item by work item id.
Use this fallback only when initiatives is false.
"""

SERVER_INSTRUCTIONS = WORK_ITEM_TYPE_SCOPING_INSTRUCTIONS + EPIC_INSTRUCTIONS + INITIATIVE_INSTRUCTIONS
