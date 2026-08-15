# The tool surface

**29 tools**, one per Plane resource, each taking an `action` parameter that
selects the operation. 183 actions in total.

```python
workitem(action="create", project_id=..., name="Fix login")
workitem(action="list", project_id=..., pql='state__group = "started"')
cycle(action="archive", project_id=..., cycle_id=...)
```

A compact catalogue — 29 tools, ~59k characters — loads fully in every MCP client
and leaves the context budget to the conversation.

## The shape of a resource module

One module per resource, five parts, in this order:

```python
NAME = "label"                       # 1. identity
TITLE = "Labels"

ACTIONS = (                          # 2. the declaration -- single source of truth
    Action("list", ("project_id",), ("cursor", "per_page"), read=True),
    Action("create", ("project_id", "name"), ("color", "description")),
    Action("delete", ("project_id", "label_id"), destructive=True),
)

FOOTER = "color is a hex code such as #EF4444."   # 3. cross-cutting notes

LEGACY = {"list_labels": "list", ...}             # 4. retired name -> action

def register(mcp):                                # 5. dispatch
    @mcp.tool(
        name=NAME,
        description=build_description("Labels within a project.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def label(action: Literal["list", "create", "delete"], project_id: str = "", ...):
        ...
```

`ACTIONS` generates the description and the MCP annotations, so documentation
cannot drift from behaviour. `Action(read=True)` and `Action(destructive=True)`
become `readOnlyHint` and `destructiveHint`.

Register a new resource by adding the module and one entry in `registry.py`.

## Conventions

**Parameters are plain typed defaults** — `= ""`, `= 0`, `= False`. Never
`X | None = None`: Pydantic renders every optional union as a verbose
`anyOf`-with-null block. Where `False` or `0` is a *meaningful value* distinct
from "not supplied" — a visibility of `0`, an intake status of `-2` — use
`bool | None` or `int | None` and say why in a comment.

**Validate enum-valued parameters.** They are `str` in the schema, so check them
in the dispatch with `one_of()`. An unrecognised value then returns an error
naming the permitted set, rather than being dropped from the payload — dropping it
writes the record without the field and reports success.

**Declare every parameter an action takes, and only those.** The declaration is not
just documentation: `ValidateActionArguments` checks each call against it, so an
argument belonging to a different action is refused instead of silently dropped. One
flat schema per tool cannot catch that — `query` is a real `workitem` parameter, just
not one `count` has any use for, and a `count` call carrying it validated cleanly and
then answered a different question. An argument left at its default is ignored, since
some clients pad a request with every parameter. Retired names are exempt: they arrive
with no `action` and under their own spelling.

**Guard a plan-gated resource** with `@plan_gated("<feature>")` below `@mcp.tool`, so a
402 becomes a message naming the feature rather than an error worth retrying. The
argument is only the fallback label: where the refusal names the feature itself
("Upgrade your plan to enable Epics"), that wins — one resource can trip several
gates, and `project` trips five.

**A guard names only what is absent.** Guard order is shared-prefix first: what
every action needs, then what one action needs. For a guard covering more than
one parameter use `needs()` rather than a shared condition:

```python
if error := needs(action, name=name, owned_by=owned_by):   # names owned_by only
    return error

if not name or not owned_by:                               # blames both
    return missing(action, "name", "owned_by")
```

The error string is the model's self-correction channel: naming exactly what is
absent lets it retry correctly on the next call.

**An action that accepts a `cursor` must return one.** Return `envelope(response)`
rather than `response.results`, so the caller receives `next_cursor` and can page
through the full set.

**Match the SDK's `params` type.** Some endpoints take `Mapping[str, Any]`
(`page_params`), others a Pydantic query-params model (`as_params`) and call
`.model_dump()` on it. A dict passed to the second kind raises `AttributeError`
at call time.

**Declare the type you mean; encoding is handled upstream.** A client that sends
`'["uuid"]'` for an array parameter is repaired by `CoerceArguments` middleware
before validation, driven by the schema alone. So type a list parameter as a list
and a number as a number — there is no need to widen it to `str` to survive a
client that stringifies. `coerce_list` remains for parameters genuinely declared
`str`, such as `add_ids`, where a comma is a separator.

**The surface spells it `workitem`; `plane-sdk` spells it `work_item`.** Tool
names, action names and parameters use `workitem`. SDK namespaces, keyword
arguments and model names keep `work_item`, as do Plane's PQL field names
(`work_items__release_id`). `test_vocabulary.py` pins the boundary in both
directions.

## Layout

| Path | Contents |
|---|---|
| `<resource>.py` | one module per resource |
| `registry.py` | `RESOURCES` in advertised order, plus the alias tables |
| `legacy.py` | `LegacyNames` — resolves retired tool names |
| `../../toolkit/` | shared helpers: `spec`, `runtime`, `paging`, `governance`, `transforms` |

`RESOURCES` is an explicit tuple, not a directory scan. Its order is the
advertised order and therefore a wire-format guarantee: tool definitions head a
client's prompt cache, so reordering invalidates live conversations. Append;
never re-sort. `test_resource_order_is_pinned` holds it to a literal list.

## Listing transforms

Two `Transform`s wrap the registered tools. Both implement `list_tools`/`get_tool`
only, so execution keeps the full schema and results — including
`structuredContent` — are unchanged.

- **`StripOutputSchemas`** (`toolkit/transforms.py`) drops `outputSchema` from the
  listing: roughly two thirds of the wire payload, for a field the MCP spec
  defines as a client-side validation contract and no client forwards to a model.
- **`LegacyNames`** (`legacy.py`) resolves a retired tool name to its
  `(tool, action)` pair on lookup, with `action` hidden and pre-filled.

## Retired tool names

Before consolidation this server exposed 177 tools, one per API operation. 169 of
those names still resolve. They are not advertised, so they cost nothing in the
listing, but a saved prompt or script calling `create_work_item` keeps working —
including the parameter names it shipped with (`work_item_id`, not `workitem_id`).
Each resolution is logged, so the set of remaining callers is an observation
rather than a guess.

`tests/tools/_retired_names.py` is the frozen record of all 177, and the
conformance suite asserts every one is aliased, declared unmappable, or still
registered under the same name.

An alias renames a tool; it cannot reshape one. Seven names chose between two
operations with a parameter (`manage_project_archive(archive=False)`,
`manage_release_labels(action="detach")`) and no single `(tool, action)` pair
reproduces that. Each is declared in its module's `LEGACY_UNMAPPED` with the
replacement to use, and the conformance suite holds that list to a budget.

## Scope: project vs workspace

Plane governs some resources at the workspace as well as the project — the same
resource under two SDK namespaces, with different id keyword names. The idiom a
model sees is uniform: **supply `project_id` for the project's own set, omit it
for the workspace's.**

Getting it wrong is quiet — the call succeeds against the wrong scope — so each
resource resolves scope once in a local `_scope_of`, and `test_governance.py`
pins both namespaces and both id keywords against the live SDK.

Each resource resolves scope locally rather than through a shared abstraction,
because the shapes differ: `workitem_type` is a two-way split, `workitem_property`
is three-way and also varies the method name. Keep new ones local until a common
shape is established by more than one caller.

**When the workspace owns the resource outright**, the project-scoped write is
refused. Two helpers, used together:

- `workspace_owns_resource(client, slug, resource)` reads the workspace flag that
  governs `resource`, so a caller can take the workspace path instead of provoking a
  refusal it already knows is coming. There is no single governance flag: work item
  types carry their own, while everything the governance migration moved (states,
  labels, workflows, templates, automations) shares `states_owned_by_workspace`. A
  workspace can own one and not the other, so `GOVERNED_BY` maps each resource to
  the flag that actually governs it — a newly governed resource adds one row.
- `workspace_owns(exc, field)` reads the refusal, which is what settles it: the flag
  is cached and the lockout outlives it being toggled off, so a write can still be
  refused after the flag reads false. It handles both shapes Plane uses.

`workitem_type resolve` is the worked example: ask who owns types, adopt from the
workspace catalogue and import if it does, otherwise create in the project — and
adopt anyway if the project write is refused.

## Tools

| Tool | Actions |
|---|---|
| `customer` | `list` · `retrieve` · `create` · `update` · `delete` · `list_workitems` · `manage_workitems` |
| `customer_property` | `list` · `retrieve` · `create` · `update` · `delete` · `get_values` · `set_values` |
| `customer_request` | `list` · `retrieve` · `create` · `update` · `delete` |
| `cycle` | `list` · `retrieve` · `create` · `update` · `delete` · `list_workitems` · `manage_workitems` · `transfer_workitems` · `complete` · `archive` · `unarchive` |
| `get_pql_reference` | *(no action parameter)* |
| `initiative` | `list` · `retrieve` · `create` · `update` · `delete` · `list_projects` · `add_projects` · `remove_projects` |
| `intake` | `list` · `retrieve` · `create` · `update` · `delete` |
| `label` | `list` · `retrieve` · `create` · `update` · `delete` |
| `member` | `me` · `list_workspace` · `list_project` · `list_roles` · `retrieve_role` |
| `milestone` | `list` · `retrieve` · `create` · `update` · `delete` · `list_workitems` · `manage_workitems` |
| `module` | `list` · `retrieve` · `create` · `update` · `delete` · `list_workitems` · `manage_workitems` · `archive` · `unarchive` |
| `page` | `list` · `retrieve` · `create` · `update` · `archive` · `delete` · `list_workitem_pages` · `attach_to_workitem` · `detach_from_workitem` |
| `project` | `list` · `retrieve` · `create` · `update` · `delete` · `archive` · `unarchive` · `worklog_summary` · `get_features` · `update_features` |
| `project_estimate` | `retrieve` · `create` · `update` · `delete` · `link` · `list_points` · `create_points` · `update_point` · `delete_point` |
| `release` | `list` · `retrieve` · `create` · `update` · `delete` · `get_changelog` · `update_changelog` · `list_workitems` · `manage_workitems` |
| `release_label` | `list` · `create` · `update` · `delete` · `attach` · `detach` |
| `release_tag` | `list` · `retrieve` · `create` · `update` · `delete` |
| `state` | `list` · `retrieve` · `create` · `update` · `delete` |
| `template` | `list` · `create` · `update` · `delete` |
| `work_log` | `list` · `create` · `update` · `delete` |
| `workitem` | `list` · `list_archived` · `retrieve` · `retrieve_by_identifier` · `search` · `count` · `create` · `update` · `delete` · `archive` · `manage_assignee` · `manage_label` |
| `workitem_activity` | `list` · `retrieve` |
| `workitem_attachment` | `list` · `read` · `download_url` · `upload_from_url` · `delete` |
| `workitem_comment` | `list` · `retrieve` · `create` · `update` · `delete` |
| `workitem_link` | `list` · `retrieve` · `create` · `update` · `delete` |
| `workitem_property` | `list` · `retrieve` · `create` · `update` · `delete` · `manage_type_properties` · `list_options` · `retrieve_option` · `create_option` · `update_option` · `delete_option` · `get_value` · `set_value` · `delete_value` |
| `workitem_relation` | `list` · `create` · `delete` · `list_definitions` · `create_definition` · `update_definition` · `delete_definition` |
| `workitem_type` | `list` · `retrieve` · `resolve` · `create` · `update` · `delete` · `import_to_project` |
| `workspace` | `get_features` · `update_features` |

Every tool's own description lists its actions with their required and optional
parameters; that description is generated from `ACTIONS` and is the authoritative
reference at call time.

## Epics

There are no epic tools. An epic is a work item whose type is named "Epic":

1. `workitem_type resolve` with `project_id` and `name="Epic"` → `id` is the `type_id`.
2. `workitem create` with that `type_id`.
3. `workitem list` with `pql='type = "<type id>"'`.

## Tests

```bash
pytest tests/tools -q        # no network, no credentials
```

Every action of every resource runs against `SpyClient`, a stand-in that binds
each call against the genuine `plane-sdk` signature and type-checks the
arguments. Payload-shape mistakes a plain mock would accept — a flat body where
the SDK wants a nested model, a dict where it wants a Pydantic object — are
caught here rather than at runtime.

| File | Guarantees |
|---|---|
| `test_conformance.py` | Surface-wide invariants: tool count, listing size, strict-mode schemas, annotations derived from `ACTIONS`, catalogue order pinned, every retired name accounted for |
| `test_dispatch.py` | Every action reaches the SDK with well-typed arguments; called bare, it names what is missing instead of issuing a request |
| `test_guards.py` | Omitting one declared required parameter names *that* parameter and no other |
| `test_pagination.py` | An action declaring `cursor` returns a `next_cursor`; an unpaginated endpoint does not advertise one |
| `test_vocabulary.py` | `workitem` everywhere a model reads; `work_item` preserved in SDK calls and retired names |
| `test_references.py` | Every backticked `tool action` in a description, in the server instructions, and in the PQL reference resolves |
| `test_governance.py` | Project-vs-workspace scope pinned to its namespace and id keyword |
| `test_workitem_property.py` | Values reach the SDK in the type the property expects; malformed option JSON and absent scopes are reported, not swallowed |
| `test_attachments.py` | The image-versus-text return channel, size limits, and the SSRF guard |
