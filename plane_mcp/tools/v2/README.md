# The consolidated tool surface

28 tools instead of 177, advertising 55k characters instead of 500k — an 89%
reduction with the same capability.

This is not a cost optimisation. The published tool caps of several MCP clients
sit below 177 (Cursor 40, Windsurf 100, Antigravity 100, VS Code Copilot 128),
and a client that truncates the listing silently makes the tools past its cap
unreachable. The flat surface is not *expensive* on those clients; it is
*incomplete*. Fitting inside every cap is the point.

> "tools v2" is this server's tool surface, not Plane's API v2. They version
> independently.

## The shape of a resource module

One module per resource, one tool per module, one `action` parameter that
selects the operation. Every module has the same five parts, in this order:

```python
NAME = "label"                       # 1. identity
TITLE = "Labels"

ACTIONS = (                          # 2. the declaration -- the single source of truth
    Action("list", ("project_id",), ("cursor", "per_page"), read=True),
    Action("create", ("project_id", "name"), ("color", "description")),
    Action("delete", ("project_id", "label_id"), destructive=True),
)

FOOTER = "color is a hex code such as #EF4444."   # 3. cross-cutting notes

LEGACY = {"list_labels": "list", ...}             # 4. v1 name -> action

def register(mcp):                                # 5. dispatch
    @mcp.tool(
        name=NAME,
        description=build_description("Labels within a project.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def label(action: Literal["list", "create", "delete"], project_id: str = "", ...):
        ...
```

`ACTIONS` generates the description and the annotations, so documentation cannot
drift from behaviour. The conformance suite asserts the `Literal` matches
`ACTIONS`, that every documented parameter exists, and that the description was
generated rather than hand-written.

## Rules that are easy to break

**Parameters are plain typed defaults** — `= ""`, `= 0`, `= False`. Never
`X | None = None`: Pydantic renders every optional union as a verbose
`anyOf`-with-null block, and that verbosity is most of what this surface exists
to remove. Where `False` or `0` is a *meaningful value* distinct from "not
supplied" — a feature toggle, an intake status of `-2` — use `bool | None` or
`int | None` and say why in a comment.

**A dropped value must become an error, not a default.** v1 typed its enums as
`Literal`, so a bad value was rejected before the call. Here they are `str`, so
the dispatch has to check them. Without that check a bad value is silently
dropped and the caller gets a plausible wrong answer — an unfiltered list, a
release created with the default status — that reads as success.

**A guard names only what is absent.** Guard order is shared-prefix first: check
what every action needs, then what one action needs. For a guard covering more
than one parameter, use `needs()` rather than a shared condition —

```python
if error := needs(action, name=name, owned_by=owned_by):   # names owned_by only
    return error

if not name or not owned_by:                               # blames both
    return missing(action, "name", "owned_by")
```

The error string is the model's self-correction channel, so a message that
blames a parameter the caller supplied costs a whole round trip.
`test_guards.py` enforces this for every declared required parameter.

**Match the SDK's `params` type.** Some endpoints take `Mapping[str, Any]`
(`page_params`), others take a Pydantic query-params model (`as_params`) and
call `.model_dump()` on it. A dict passed to the second kind raises
`AttributeError` at call time — `SpyClient` in the test suite rejects it up
front.

## The listing transforms

Two `Transform`s wrap the registered tools:

- `StripOutputSchemas` (`plane_mcp/toolkit/transforms.py`) removes `outputSchema`
  from the listing. It was two thirds of the v1 payload and no client forwards it
  to a model. It is catalogue-agnostic, so it lives in the toolkit.
- `LegacyNames` (`tools/v2/legacy.py`) resolves each v1 tool name to its
  `(tool, action)` pair on lookup, with `action` hidden and pre-filled. It
  encodes this catalogue's history, so it stays with the catalogue.

Both implement `list_tools`/`get_tool` only. Execution keeps the full schema, so
results — including `structuredContent` — are unchanged.

## Legacy names

169 of the 177 v1 names resolve through `LegacyNames`. They are not advertised;
they are accepted, so a saved prompt or a script that calls `create_work_item`
keeps working.

An alias renames a tool; it cannot reshape one. Seven v1 tools chose between two
operations with a parameter (`manage_project_archive(archive=False)`,
`manage_release_labels(action="detach")`), and no single `(tool, action)` pair
reproduces that. They are declared in each module's `LEGACY_UNMAPPED` with the
replacement to use, and the conformance suite holds that list to a budget.

## Tests

```bash
pytest tests/tools/v2/ -q
```

- `test_conformance.py` — surface-wide invariants: tool count within client
  caps, listing within budget, strict-mode compatible schemas, annotations
  correct, catalogue order pinned to a literal list, every legacy name
  accounted for.
- `test_dispatch.py` — every action of every resource, executed against
  `SpyClient`, which binds each call against the genuine SDK signature and
  type-checks the arguments. Also asserts that an action called bare names what
  it needs instead of reaching the network.
- `test_pagination.py` — every action declaring `cursor` must return a
  `next_cursor`, and an action whose endpoint does not paginate must not
  advertise one. Derived from `ACTIONS`, so a new resource is covered
  automatically.
- `test_guards.py` — omitting one declared required parameter must produce an
  error naming that parameter and no other.
- `test_work_item_property.py` — the defects where this resource answered
  plausibly instead of correctly: type-guessed values, swallowed option JSON,
  and errors reported as an empty result.
- `test_attachments.py` — the actions needing populated state or an outbound
  fetch, including the image-versus-text return channel and the SSRF guard.
- `test_references.py` — every backticked `tool action` in a description
  resolves, and no description points at a retired v1 tool name. Descriptions
  are instructions a model follows literally; a dead pointer is a real defect.
- `test_governance.py` — for resources Plane governs at both scopes, each scope
  is pinned to its namespace and id keyword, and the declaration is checked
  against the live SDK.

## Governance: workspace scope vs project scope

Plane governs some resources at the workspace as well as the project — the same
resource under two SDK namespaces, with different id keyword names and sometimes
a smaller set of operations. `scope.py` declares that once per resource instead
of each module hand-rolling `if project_id:`.

The idiom the model sees is uniform: **supply `project_id` for the project's own
set, omit it for the workspace's.** Work item types are the only governed
resource today; `scope.py` records what adding another takes.

## Where the code lives

This package holds resource modules and the catalogue, nothing else:

| Path | What |
|---|---|
| `tools/v2/<resource>.py` | one module per resource — the 28 tools |
| `tools/v2/registry.py` | `RESOURCES`, in advertised order, plus the alias tables |
| `tools/v2/legacy.py` | `LegacyNames` — a v1 migration artefact; dies with v1 |
| `tools/v2/scope.py` | project-vs-workspace governance declarations |
| `plane_mcp/toolkit/` | everything shared: `spec`, `runtime`, `paging`, `transforms` |

The helpers used to live here as `_`-prefixed modules, where the underscore was
not a privacy marker — it was the filter the discovery loop used to tell helpers
from resources. That made an ordinary helper's *filename* load-bearing and made
`spec`, imported by all 28 resource modules, look private. They now sit in
`plane_mcp/toolkit/`, outside a directory that is scheduled to be renamed when
v1 is dropped.

`RESOURCES` is an explicit tuple rather than a `pkgutil` scan. The order is a
wire-format guarantee — tool definitions head a client's prompt cache — and
`test_resource_order_is_pinned` holds it to a literal list, so a change shows up
as a diff instead of as a silent cache-buster.

## Adding a resource

1. Copy the five-part shape from `label.py`.
2. Declare `ACTIONS` first; the description and annotations follow from it.
3. Map every v1 name in `LEGACY`, or explain the break in `LEGACY_UNMAPPED`.
4. Append the module to `RESOURCES` in `registry.py` and to `CATALOGUE` in
   `test_conformance.py`. Append — do not re-sort; re-sorting invalidates every
   live client's prompt cache.
5. Run `pytest tests/tools/v2/ -q`. The dispatch suite parametrises over
   `ACTIONS`, so the new resource is exercised action by action.
