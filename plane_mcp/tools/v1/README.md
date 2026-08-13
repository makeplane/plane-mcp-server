# Tool surface `v1`

The original flat surface: **177 tools**, one per API operation.

> **Deprecated.** Kept for one major release to ease migration, and the server
> logs a warning on start-up when it is selected. New work should target the
> default surface.

```bash
export PLANE_MCP_TOOLS_VERSION=v1
```

"v1" is the version of *this server's tool surface*. It is unrelated to Plane's
API versions.

## Shape

One function per operation, registered directly. Each returns a Pydantic model
from `plane-sdk`.

```python
def register_label_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def create_label(project_id: str, name: str, color: str | None = None) -> Label:
        """Create a label in a project.

        Args:
            project_id: UUID of the project
            name: Label name
            color: Hex colour code
        """
        client, workspace_slug = get_plane_client_context()
        return client.labels.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateLabel(name=name, color=color),
        )
```

Conventions:

- The docstring is the tool description a model reads. Keep the `Args:` section
  accurate — it is the only place a parameter is explained.
- Enum-valued parameters are `Literal[...]`, so an invalid value is rejected
  before the call.
- Optional parameters are `T | None = None`.
- Credentials come from `get_plane_client_context()`, never from arguments.

## Layout

| Path | Contents |
|---|---|
| `__init__.py` | `register_tools(mcp)` — calls every `register_*_tools` |
| `<resource>.py` | one module per resource |
| `customers/`, `releases/` | resources large enough to warrant a package |

A new tool needs a `register_*_tools` function and one line in `__init__.py`.

## Tools

| Resource | Tools |
|---|---|
| **Customer properties** | `create_customer_property` · `delete_customer_property` · `get_customer_property_values` · `list_customer_properties` · `retrieve_customer_property` · `set_customer_property_values` · `update_customer_property` |
| **Customer requests** | `create_customer_request` · `delete_customer_request` · `list_customer_requests` · `retrieve_customer_request` · `update_customer_request` |
| **Customers** | `create_customer` · `delete_customer` · `list_customer_work_items` · `list_customers` · `manage_customer_work_items` · `retrieve_customer` · `update_customer` |
| **Cycles** | `complete_cycle` · `create_cycle` · `delete_cycle` · `list_cycle_work_items` · `list_cycles` · `manage_cycle_archive` · `manage_cycle_work_items` · `retrieve_cycle` · `transfer_cycle_work_items` · `update_cycle` |
| **Initiatives** | `create_initiative` · `delete_initiative` · `list_initiative_projects` · `list_initiatives` · `manage_initiative_projects` · `retrieve_initiative` · `update_initiative` |
| **Intake queue** | `create_intake_work_item` · `delete_intake_work_item` · `list_intake_work_items` · `retrieve_intake_work_item` · `update_intake_work_item` |
| **Labels** | `create_label` · `delete_label` · `list_labels` · `retrieve_label` · `update_label` |
| **Members and roles** | `get_me` · `get_project_members` · `get_workspace_members` · `list_roles` · `retrieve_role` |
| **Milestones** | `create_milestone` · `delete_milestone` · `list_milestone_work_items` · `list_milestones` · `manage_milestone_work_items` · `retrieve_milestone` · `update_milestone` |
| **Modules** | `create_module` · `delete_module` · `list_module_work_items` · `list_modules` · `manage_module_archive` · `manage_module_work_items` · `retrieve_module` · `update_module` |
| **PQL reference** | `get_pql_reference` |
| **Pages** | `attach_page_to_work_item` · `create_page` · `detach_page_from_work_item` · `list_pages` · `list_work_item_pages` · `retrieve_page` |
| **Project estimates** | `create_project_estimate` · `create_project_estimate_points` · `delete_project_estimate` · `delete_project_estimate_point` · `get_project_estimate` · `link_estimate_to_project` · `list_project_estimate_points` · `update_project_estimate` · `update_project_estimate_point` |
| **Projects** | `create_project` · `delete_project` · `get_project_worklog_summary` · `list_projects` · `manage_project_archive` · `retrieve_project` · `update_project` · `update_project_features` |
| **Release labels** | `create_release_label` · `delete_release_label` · `list_release_labels` · `manage_release_labels` · `update_release_label` |
| **Release tags** | `create_release_tag` · `delete_release_tag` · `list_release_tags` · `retrieve_release_tag` · `update_release_tag` |
| **Releases** | `create_release` · `delete_release` · `get_release_changelog` · `list_release_work_items` · `list_releases` · `manage_release_work_items` · `retrieve_release` · `update_release` · `update_release_changelog` |
| **Work item activity** | `list_work_item_activities` · `retrieve_work_item_activity` |
| **Work item attachments** | `delete_work_item_attachment` · `get_work_item_attachment_download_url` · `list_work_item_attachments` · `read_work_item_attachment` · `upload_work_item_attachment_from_url` |
| **Work item comments** | `create_work_item_comment` · `delete_work_item_comment` · `list_work_item_comments` · `retrieve_work_item_comment` · `update_work_item_comment` |
| **Work item links** | `create_work_item_link` · `delete_work_item_link` · `list_work_item_links` · `retrieve_work_item_link` · `update_work_item_link` |
| **Work item properties** | `create_work_item_property` · `create_work_item_property_option` · `delete_work_item_property` · `delete_work_item_property_option` · `delete_work_item_property_value` · `get_work_item_property_value` · `list_work_item_properties` · `list_work_item_property_options` · `manage_work_item_type_properties` · `retrieve_work_item_property` · `retrieve_work_item_property_option` · `set_work_item_property_value` · `update_work_item_property` · `update_work_item_property_option` |
| **Work item relations** | `create_work_item_relation` · `create_work_item_relation_definition` · `delete_work_item_relation_definition` · `list_work_item_relation_definitions` · `list_work_item_relations` · `remove_work_item_relation` · `update_work_item_relation_definition` |
| **Work item types** | `create_work_item_type` · `delete_work_item_type` · `import_work_item_types_to_project` · `list_work_item_types` · `resolve_work_item_type` · `retrieve_work_item_type` · `update_work_item_type` |
| **Work items** | `count_work_items` · `create_work_item` · `delete_work_item` · `list_archived_work_items` · `list_work_items` · `manage_work_item_archive` · `manage_work_item_assignee` · `manage_work_item_label` · `retrieve_work_item` · `retrieve_work_item_by_identifier` · `search_work_items` · `update_work_item` |
| **Work logs** | `create_work_log` · `delete_work_log` · `list_work_logs` · `update_work_log` |
| **Workflow states** | `create_state` · `delete_state` · `list_states` · `retrieve_state` · `update_state` |
| **Workspace settings** | `get_features` · `update_workspace_features` |

## Epics

There are no epic tools. An epic is a work item whose type is named "Epic":

1. `resolve_work_item_type` with `project_id` and `name="Epic"` → `id` is the `type_id`.
2. `create_work_item` with that `type_id`.
3. `list_work_items` with `pql='type = "<type id>"'`.

## Tests

```bash
pytest tests/test_work_items.py -q      # offline, no credentials
pytest tests/test_docs.py -q            # this README matches the registered tools
```

End-to-end coverage comes from `tests/test_integration.py`, which drives a running
server against a live workspace. See the repository README for how to run it.
