# Consolidated tool surface (v2)

**177 tools → 29**, one action-dispatch tool per resource.

Both surfaces ship in the same package and coexist. **v1 (177 tools) is the default**;
v2 is opt-in via `--v2`, so existing clients are unaffected.

> **Experimental.** Behaviour is ported faithfully from `plane_mcp/tools/`, but write
> paths are only partly tested — see [Known issues](#known-issues). Full analysis and
> rollout plan: [`docs/tool-consolidation-plan.md`](../../docs/tool-consolidation-plan.md).

---

## Running it

```bash
plane-mcp-server stdio --v2      # 29 consolidated tools
plane-mcp-server stdio           # 177 tools (default, unchanged)
plane-mcp-server http --v2       # works with every mode
```

## Client config

### From PyPI

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio", "--v2"],
      "env": {
        "PLANE_API_KEY": "plane_api_...",
        "PLANE_WORKSPACE_SLUG": "your-workspace",
        "PLANE_BASE_URL": "https://api.plane.so"
      }
    }
  }
}
```

### From the repo (unreleased changes)

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/makeplane/plane-mcp-server",
        "plane-mcp-server", "stdio", "--v2"
      ],
      "env": {
        "PLANE_API_KEY": "plane_api_...",
        "PLANE_WORKSPACE_SLUG": "your-workspace",
        "PLANE_BASE_URL": "https://api.plane.so"
      }
    }
  }
}
```

> `uvx` caches git installs. With no ref pinned it resolves the default branch once and
> reuses that build — add `--refresh` to pick up new commits, or pin a tag (`@v0.3.0`)
> for anything reproducible.

### Hosted

```json
{ "mcpServers": { "plane": { "type": "http", "url": "https://mcp.plane.so/http/mcp" } } }
```

Whether the hosted deployment serves v1 or v2 is a deployment flag (`--v2`), not a client
setting.

### Environment

| Variable | Required | Notes |
|---|---|---|
| `PLANE_API_KEY` | yes (stdio) | Workspace API token |
| `PLANE_WORKSPACE_SLUG` | yes (stdio) | Target workspace |
| `PLANE_BASE_URL` | no | Defaults to `https://api.plane.so` |
| `PLANE_MCP_V2_VARIANT` | no | `typed` (default) or `str` — see [Variants](#variants) |

**Surface selection is a flag, not an env var, on purpose.** A config that loses `--v2`
fails visibly; a config that loses an env var would silently serve 177 tools.

---

## Calling convention

Every tool takes a required `action` plus that action's params. The tool **description**
lists every action with its required and optional params — that is the authoritative
reference, since JSON Schema can no longer express "required for *this* action".

```jsonc
{ "action": "list",   "project_id": "<uuid>" }
{ "action": "create", "project_id": "<uuid>", "name": "bug", "color": "#ef4444" }
{ "action": "update", "project_id": "<uuid>", "label_id": "<uuid>", "color": "#3b82f6" }
```

Only the fields you pass are changed on `update`. Mistakes return readable strings rather
than schema errors:

```
Error: unknown action 'bogus'. Must be one of: list, retrieve, create, update, delete.
Error: action 'retrieve' requires: work_item_id.
```

### The 29 tools

`customer` · `customer_property` · `customer_request` · `cycle` · `get_pql_reference` ·
`initiative` · `intake` · `label` · `member` · `milestone` · `module` · `page` ·
`project` · `project_estimate` · `release` · `release_label` · `release_tag` · `state` ·
`work_item` · `work_item_activity` · `work_item_attachment` · `work_item_comment` ·
`work_item_link` · `work_item_property` · `work_item_property_value` ·
`work_item_relation` · `work_item_type` · `work_log` · `workspace`

`get_pql_reference` is unchanged 1:1 from v1 and takes no `action`.

### Naming notes

- **`action` is reserved.** Four v1 tools had their own `action` parameter
  (`manage_release_work_items`, `manage_release_labels`, `manage_customer_work_items`,
  `manage_initiative_projects`); those sub-verbs are now `operation` or `op`.
- Archive/unarchive are **separate actions**, not a boolean.
- `manage_*_work_items` keeps its combined add/remove shape as one `manage_work_items`
  action, because the v1 tool supports doing both in one call.

---

## Variants

`PLANE_MCP_V2_VARIANT` selects what tools return:

| Value | Returns | Wire payload | Trade |
|---|---|---:|---|
| **`typed`** (default) | Pydantic models | ~59.8k tok | Keeps typed `structuredContent` |
| `str` | JSON strings | ~15.8k tok | No typed output; `work_item_attachment.read` cannot return images |

The gap is almost entirely `outputSchema`. Excluding output schemas the two are within
**1.5%** — and an Anthropic tool definition has no output-schema field, so those bytes may
never reach the model. Run `spike/bench/probe_model_tokens.py` to settle it; §10 of the
plan doc has the analysis.

**Variant BD (typed + schema compression) is measured but not implemented.** The server
never calls `compress()`. Do not try to configure it.

---

## Adding or editing a module

Each `<name>.py` exports `register_typed(mcp)` and `register_str(mcp)` over a shared
`_dispatch()`. Helpers (`missing`, `bad_action`, `json_out`, `page_params`, `opt`) live in
`_common.py`, which also documents the schema-size conventions.

`intake.py` is the reference implementation — mirror it.

Two conventions that matter for payload size:

- **Plain typed defaults** (`= ""`, `= 0`, `= False`), not `| None = None`. Pydantic
  renders every `X | None` as a verbose `anyOf`-with-null block. Use `| None` only where
  the zero value is meaningful (a tri-state boolean, a status enum including `0`).
- **Required-field enforcement lives in `_dispatch`** via `missing()`, documented per
  action in the docstring. There is no schema-level `required` beyond `action`, so an
  inaccurate docstring is a real bug.

After editing: `.venv/bin/python spike/bench/check_v2.py`

---

## Known issues

- **`work_item_activity` fails** with a pydantic `int_from_float` error. Pre-existing —
  the SDK types `epoch` as `int` but the API returns a float. v1's
  `list_work_item_activities` fails identically. Not a consolidation regression.
- **`delete` returns `null`** on the `typed` variant, so success is indistinguishable from
  a no-op without a follow-up read (`str` returns `"Done"`). Faithful to v1, but worth
  fixing.
- **Feature-gated tools** error on plans that lack them: `work_item_relation` (HTTP 402),
  `initiative`, `work_log`, `project_estimate`, `work_item_property_value`.
- **`Literal` enums are now plain `str`** in places (`release.status`, `cycle.list.status`,
  `member.namespace`). Values are in the tool description; bad input fails at pydantic or a
  runtime guard rather than schema validation.
- **Pagination is inconsistent.** Most actions match their v1 tool, but `release` and
  `release_tag` unwrap to `.results` where v1 returned the full envelope — losing
  `next_cursor`. Fix before productionising.

## Status

**Verified:** 29/29 modules register in both variants; one read-only live call per tool
(22 OK, 5 feature-gated, 1 pre-existing failure, 0 consolidation failures); a full
create → update → delete round trip through a real MCP client; `uvx` end-to-end for both
surfaces; v1 unchanged at 177 tools; 56/56 unit tests pass.

**Not verified:** write paths for most tools, tool-selection accuracy at scale, and whether
output schemas reach the model.
