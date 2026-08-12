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

**Guard order is shared-prefix first.** Check what every action needs, then what
one action needs, so each `missing()` names only what is actually absent.

**Match the SDK's `params` type.** Some endpoints take `Mapping[str, Any]`
(`page_params`), others take a Pydantic query-params model (`as_params`) and
call `.model_dump()` on it. A dict passed to the second kind raises
`AttributeError` at call time — `SpyClient` in the test suite rejects it up
front.

## The listing transforms

Two `Transform`s wrap the registered tools:

- `StripOutputSchemas` removes `outputSchema` from the listing. It was two
  thirds of the v1 payload and no client forwards it to a model.
- `LegacyNames` resolves each v1 tool name to its `(tool, action)` pair on
  lookup, with `action` hidden and pre-filled.

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
  correct, deterministic ordering, every legacy name accounted for.
- `test_dispatch.py` — every action of every resource, executed against
  `SpyClient`, which binds each call against the genuine SDK signature and
  type-checks the arguments. Also asserts that an action called bare names what
  it needs instead of reaching the network.
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
a smaller set of operations. `_scope.py` declares that once per resource instead
of each module hand-rolling `if project_id:`.

The idiom the model sees is uniform: **supply `project_id` for the project's own
set, omit it for the workspace's.** Work item types are the only governed
resource today; `_scope.py` records what adding another takes.

## Adding a resource

1. Copy the five-part shape from `label.py`.
2. Declare `ACTIONS` first; the description and annotations follow from it.
3. Map every v1 name in `LEGACY`, or explain the break in `LEGACY_UNMAPPED`.
4. Run `pytest tests/tools/v2/ -q`. The new resource is picked up
   automatically — discovery is by module, and the dispatch suite parametrises
   over `ACTIONS`.
