# Consolidated tool surface (spike)

An experimental rebuild of the Plane MCP tool surface: **177 tools → 29**, one
action-dispatch tool per resource.

> **This is a spike on `spike/tool-consolidation-v2`, not shipped code.** It talks to a
> real Plane workspace and performs real creates, updates, and deletes. Point it at a
> test workspace. See [Known issues](#known-issues) before trusting a result.

Full analysis, measurements, and the rollout proposal: [`docs/tool-consolidation-plan.md`](../docs/tool-consolidation-plan.md).

---

## Quick start

```bash
uv venv .venv && uv pip install -e . --python .venv/bin/python
PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... .venv/bin/python spike/server_v2.py
```

It speaks MCP over stdio. On startup it logs to **stderr** (stdout is the protocol
channel and stays clean):

```
[spike v2-typed] registered 29 modules
```

## Wiring it into a client

```json
{
  "mcpServers": {
    "plane-v2": {
      "command": "/ABSOLUTE/PATH/TO/plane-mcp-server/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/plane-mcp-server/spike/server_v2.py"],
      "env": {
        "PLANE_BASE_URL": "https://api.plane.so",
        "PLANE_API_KEY": "plane_api_...",
        "PLANE_WORKSPACE_SLUG": "your-workspace"
      }
    }
  }
}
```

> **Use the absolute file path, not `-m spike.server_v2`.** `python -m` resolves against
> the *current working directory*, which for a client-launched server is wherever the
> config lives — usually not this repo. It fails with `ModuleNotFoundError: No module
> named 'spike'`, surfacing as a `-32000` connection error. Running the file directly
> works from any cwd because the script puts the repo root on `sys.path` itself.

Or register it with the Claude Code CLI:

```bash
claude mcp add plane-v2 --scope local \
  -e PLANE_BASE_URL=https://api.plane.so \
  -e PLANE_API_KEY=... \
  -e PLANE_WORKSPACE_SLUG=... \
  -- /ABS/PATH/.venv/bin/python /ABS/PATH/spike/server_v2.py
```

`.mcp.json` is gitignored in this repo — but check your own before committing one with a
key in it.

### Environment

| Variable | Required | Notes |
|---|---|---|
| `PLANE_API_KEY` | yes | Workspace API token |
| `PLANE_WORKSPACE_SLUG` | yes | Target workspace |
| `PLANE_BASE_URL` | no | Defaults to `https://api.plane.so` |
| `PLANE_MCP_V2_VARIANT` | no | `typed` (default) or `str` — see below |

Values also load from `.env.test.local` at the repo root if present; real environment
variables always win.

---

## Using the tools

Every tool takes a required `action` plus the params that action needs. The tool's
description lists every action and its required/optional params — that is the
authoritative reference, since JSON Schema can no longer express "required for *this*
action".

```jsonc
// list labels
{ "action": "list", "project_id": "<uuid>" }

// create one
{ "action": "create", "project_id": "<uuid>", "name": "bug", "color": "#ef4444" }

// update — only the fields you pass change
{ "action": "update", "project_id": "<uuid>", "label_id": "<uuid>", "color": "#3b82f6" }
```

Mistakes come back as readable strings, not schema errors:

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

`get_pql_reference` is unchanged 1:1 from the baseline and takes no `action`.

### Naming notes

- **`action` is reserved.** Four baseline tools had their own `action` parameter
  (`manage_release_work_items`, `manage_release_labels`, `manage_customer_work_items`,
  `manage_initiative_projects`). Those sub-verbs are now `operation` or `op` — check the
  tool description.
- Archive/unarchive are **separate actions**, not a boolean flag.
- `manage_*_work_items` keeps its combined add/remove shape as one `manage_work_items`
  action, because the source supports doing both in one call.

---

## Variants

Set `PLANE_MCP_V2_VARIANT`:

| Value | Returns | Wire payload | Trade |
|---|---|---:|---|
| **`typed`** (default) | Pydantic models | ~59.8k tok | Keeps typed `structuredContent` |
| `str` | JSON strings | ~15.8k tok | No typed output; `work_item_attachment.read` cannot return images |

The wire difference is almost entirely `outputSchema`. Excluding output schemas the two
are within **1.5%** of each other — and an Anthropic tool definition has no
output-schema field, so those bytes may never reach the model at all. That is the open
question; see §10 of the plan doc and run `bench/probe_model_tokens.py` to settle it.

**Variant BD (typed + schema compression) is measured but not implemented** — the server
never calls `compress()`. Do not configure it; it does not exist.

---

## Layout

```
spike/
├── server_v2.py   stdio entrypoint
├── v2/            the 29 consolidated modules + _common.py helpers
└── bench/         measurement and test tooling (see below)
```

Each `v2/<name>.py` exports `register_typed(mcp)` and `register_str(mcp)` over a shared
`_dispatch()`. `v2/intake.py` is the reference implementation — mirror it when adding a
module.

### `bench/`

Throwaway tooling that produced the numbers in the plan doc. Not part of the deliverable.

| Script | What it does |
|---|---|
| `check_v2.py` | Registers every module both ways; catches import errors and name mismatches |
| `measure_all.py` | Reproduces the full A/B/C/BD/D payload table |
| `live_smoke.py` | One read-only call against every tool on a live workspace |
| `probe_model_tokens.py` | **Not yet run.** Measures real model-facing cost; needs `ANTHROPIC_API_KEY` |
| `compress.py` / `verify.py` | Lossless schema compressor + its proof. Only relevant if BD is built |

```bash
.venv/bin/python spike/bench/check_v2.py     # 29 ok, 0 failing
.venv/bin/python spike/bench/measure_all.py
.venv/bin/python spike/bench/live_smoke.py   # needs .env.test.local
```

---

## Known issues

- **`work_item_activity` fails** with a pydantic `int_from_float` error. Pre-existing —
  the SDK types `epoch` as `int` but the API returns a float. The baseline
  `list_work_item_activities` fails identically. Not a consolidation regression.
- **`delete` returns `null`** on the `typed` variant, so success is indistinguishable
  from a no-op without a follow-up read. (`str` returns `"Done"`.) Faithful to the
  baseline, but worth fixing before shipping.
- **Feature-gated tools** error on plans that lack them: `work_item_relation` (HTTP 402),
  `initiative`, `work_log`, `project_estimate`, `work_item_property_value`.
- **`Literal` enums are now plain `str`** in several places (`release.status`,
  `cycle.list.status`, `member.namespace`). Values are documented in the tool
  description; bad input fails at pydantic or a runtime guard instead of schema
  validation.
- **Pagination is inconsistent.** Most actions match their source tool, but `release` and
  `release_tag` unwrap to `.results` where the source returned the full envelope — losing
  `next_cursor`. Fix before productionising.

## Status

Verified: 29/29 modules register; compressor lossless on 316/316 live schemas; one
read-only live call per tool (22 OK, 5 feature-gated, 1 pre-existing failure, 0
consolidation failures); a full create → update → delete round trip through a real MCP
client.

Not verified: write paths for most tools, tool-selection accuracy at scale, and whether
output schemas reach the model.
