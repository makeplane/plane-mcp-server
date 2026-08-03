# Tool Surface Consolidation Plan

**Status:** Draft / proposal
**Author:** Manish Gupta
**Repo:** `makeplane/plane-mcp-server`
**Baseline commit:** `96cf4d5` (v0.2.11)

---

## 1. Problem

The server registers **177 tools**, unconditionally, on every transport. Measured against the
live `tools/list` payload, that surface costs:

```
TOOLS  = 177
TOTAL  = 502,596 chars  ≈  125,650 tokens
```

**~125k tokens of tool schema is sent before the user says a word.** On a 200k-token context
window that is >60% consumed at rest. Two concrete consequences:

1. **Context cost** — every request on every client pays it.
2. **Hard client caps** — several MCP clients cap the number of tools they will load
   (well under 177). Tools beyond the cap are silently dropped, which is a functional
   break, not just a cost.

### Where the cost actually is

| Component | Tokens | Share |
|:---|---:|---:|
| **`outputSchema`** | **84,618** | **67%** |
| `description` | 18,629 | 15% |
| `inputSchema` | 17,004 | 14% |
| names / wrapper | ~5,400 | 4% |

The mean tool takes **4.5 parameters**. These tools are not fat on the input side.
`update_intake_work_item` has a **535-char** input schema and a **19,410-char** output schema.

**Root cause of the output-schema blowup:**

- Tools return Pydantic models from `plane-sdk`; FastMCP auto-derives `outputSchema` from the
  return annotation.
- `$defs` is **empty** — FastMCP *inlines* every nested model rather than `$ref`-ing it. So
  `IntakeWorkItem.issue_detail` expands the entire `WorkItem`, which expands `module`, `cycle`,
  … repeated in full at every occurrence.
- Pydantic v2 emits each `X | None = None` field as
  `"anyOf":[{"type":"string"},{"type":"null"}],"default":null` — ~60 chars to say "optional
  string". The intake schema contains **139** such blocks.

### Concentration

```
50% of all tokens = top 27 tools (15% of tools)
80% of all tokens = top 84 tools (47% of tools)

4 intake tools alone = 20,427 tok = 16% of the entire tool list
```

---

## 2. Three levers, in cost-effectiveness order

| # | Lever | Effect | Effort | Risk |
|---|:---|---:|:---|:---|
| **1** | Optional-param defaults (`= ""` not `\| None = None`) | shrinks `inputSchema` | Low, mechanical | Very low |
| **2** | Drop / trim `outputSchema` | **125,650 → 41,030 (−67%)**, measured | Low | **Medium** — see §6 |
| **3** | Action-dispatch consolidation (177 → 29) | remaining input dedup + docstring collapse | High, 33 modules | Medium |

**Sequencing matters.** Levers 2 and 3 overlap heavily: a consolidated tool that still returns
typed Pydantic unions keeps most of its output-schema weight. Doing lever 3 first means a
33-module rewrite that captures the *smaller* share of the benefit. Do 1 → 2 → 3.

Lever 1 is worth doing immediately and independently — it is a mechanical signature change with
no behavioural effect and no API surface change.

---

## 3. Target surface: 177 → 29 tools

One resource-oriented tool per domain, dispatching on a required `action` parameter.

### Tool pattern (post-refactor)

```python
@mcp.tool()
def label(
    action: str,
    project_id: str = "",
    label_id: str = "",
    name: str = "",
    color: str = "",
    description: str = "",
    parent: str = "",
    sort_order: float = 0,
) -> str:
    """Manage project labels. Actions:
    list (project_id);
    retrieve (project_id, label_id);
    create (project_id, name; optional color, description, parent, sort_order);
    update (project_id, label_id, plus any field to change);
    delete (project_id, label_id)."""
    client, workspace_slug = get_plane_client_context()
    if action == "list":
        if not project_id:
            return _missing(action, "project_id")
        return _json(client.labels.list(...).results)
    ...
    return _bad_action(action, ["list", "retrieve", "create", "update", "delete"])
```

Three conventions carry the design:

- **`-> str` returning `_json(...)`** — eliminates the auto-derived `outputSchema` entirely.
- **Plain typed defaults** (`= ""`, `= 0`, `= True`) instead of `| None = None` — avoids the
  `anyOf`-null bloat.
- **`_missing(action, *names)` / `_bad_action(action, allowed)`** — required-field enforcement
  moves from JSON Schema to runtime, returning an error string that tells the model exactly what
  to supply. The docstring enumerates each action's required params so the signal survives in prose.

---

## 4. Per-module plan

Projection method: `outputSchema` → 0; `inputSchema` × 0.65 (lever 1) × 0.45 (dedup across
merged actions); one consolidated docstring ≈ 1,400 chars; wrapper ≈ 120 chars. Ratios are
conservative and derived from a working consolidated implementation, not from theory.

| Target tool | Now | After | Current tok | of which outputSchema | Projected tok | Cut |
|:---|---:|---:|---:|---:|---:|---:|
| `intake` | 5 | 1 | 20,427 | 19,454 | ~479 | −98% |
| `project` | 8 | 1 | 11,518 | 9,186 | ~721 | −94% |
| `work_item` | 12 | 1 | 10,111 | 5,335 | ~1,007 | −90% |
| `work_item_property` | 11 | 1 | 8,772 | 5,277 | ~811 | −91% |
| `initiative` | 7 | 1 | 8,467 | 6,992 | ~552 | −93% |
| `project_estimate` | 9 | 1 | 6,308 | 4,589 | ~565 | −91% |
| `cycle` | 10 | 1 | 6,190 | 3,835 | ~688 | −89% |
| `customer_property` | 7 | 1 | 5,762 | 3,813 | ~584 | −90% |
| `module` | 8 | 1 | 5,082 | 3,070 | ~668 | −87% |
| `customer` | 7 | 1 | 4,724 | 2,671 | ~612 | −87% |
| `release` | 9 | 1 | 4,533 | 2,593 | ~599 | −87% |
| `work_item_type` | 7 | 1 | 3,297 | 1,708 | ~545 | −83% |
| `member` | 5 | 1 | 2,807 | 1,670 | ~534 | −81% |
| `page` | 6 | 1 | 2,578 | 1,513 | ~518 | −80% |
| `work_item_comment` | 5 | 1 | 2,497 | 1,521 | ~516 | −79% |
| `work_item_relation` | 7 | 1 | 2,470 | 836 | ~536 | −78% |
| `state` | 5 | 1 | 2,381 | 1,382 | ~529 | −78% |
| `work_item_property_value` | 3 | 1 | 2,136 | 1,377 | ~448 | −79% |
| `label` | 5 | 1 | 2,080 | 1,200 | ~511 | −75% |
| `release_tag` | 5 | 1 | 1,913 | 1,172 | ~464 | −76% |
| `customer_request` | 5 | 1 | 1,874 | 909 | ~485 | −74% |
| `milestone` | 7 | 1 | 1,849 | 750 | ~527 | −71% |
| `work_item_link` | 5 | 1 | 1,624 | 960 | ~463 | −71% |
| `release_label` | 5 | 1 | 1,549 | 703 | ~468 | −70% |
| `work_log` | 4 | 1 | 1,296 | 671 | ~464 | −64% |
| `work_item_attachment` | 5 | 1 | 1,172 | 78 | ~458 | −61% |
| `work_item_activity` | 2 | 1 | 1,019 | 733 | ~414 | −59% |
| `workspace` | 2 | 1 | 977 | 597 | ~426 | −56% |
| `pql` | 1 | 1 | 224 | 11 | 224 | unchanged |
| **TOTAL** | **177** | **29** | **125,637** | **84,618** | **~15,800** | **−87%** |

> **Floor effect.** Below ~4 source tools, a consolidated docstring costs more than it saves.
> `pql` stays 1:1 as-is. `workspace` and `work_item_activity` are marginal — merge them for
> naming consistency, not for tokens.

---

### 4.1 Detailed action maps

Grouped by how much they change. Each line is `target tool ← current tools`.

#### Large merges (7+ tools)

**`work_item`** ← 12
`list`, `list_archived`, `retrieve`, `retrieve_by_identifier`, `search`, `count`, `create`,
`update`, `delete`, `archive`, `unarchive`, `add_assignee`, `remove_assignee`, `add_label`,
`remove_label`
*Note:* the four `manage_*` sub-actions already exist as separate tools
(`manage_work_item_assignee`, `manage_work_item_label`, `manage_work_item_archive`) — they fold
in naturally as actions.

**`work_item_property`** ← 11
`list`, `retrieve`, `create`, `update`, `delete`, `list_options`, `retrieve_option`,
`create_option`, `update_option`, `delete_option`, `manage_type_properties`

**`cycle`** ← 10
`list`, `retrieve`, `create`, `update`, `delete`, `complete`, `archive`, `unarchive`,
`list_work_items`, `manage_work_items`, `transfer_work_items`

**`project_estimate`** ← 9
`get`, `create`, `update`, `delete`, `list_points`, `create_points`, `update_point`,
`delete_point`, `link_to_project`

**`release`** ← 9 (spans `releases/base.py`, `releases/changelog.py`, `releases/work_items.py`)
`list`, `retrieve`, `create`, `update`, `delete`, `get_changelog`, `update_changelog`,
`list_work_items`, `manage_work_items`

**`project`** ← 8
`list`, `retrieve`, `create`, `update`, `delete`, `archive`, `unarchive`, `update_features`,
`worklog_summary`
*Note:* `get_project_members` relocates to `member`.

**`module`** ← 8
`list`, `retrieve`, `create`, `update`, `delete`, `archive`, `unarchive`, `list_work_items`,
`manage_work_items`

**`initiative`** ← 7 · **`work_item_type`** ← 7 · **`milestone`** ← 7 ·
**`customer`** ← 7 (base + work_items) · **`customer_property`** ← 7 (properties + values) ·
**`work_item_relation`** ← 7 (relations + relation_definitions)

#### Standard CRUD merges (5–6 tools → `list`/`retrieve`/`create`/`update`/`delete`)

`intake`, `page`, `work_item_comment`, `state`, `label`, `release_tag`, `release_label`,
`customer_request`, `work_item_link`, `work_item_attachment`, `member`

`work_item_attachment` keeps non-CRUD actions: `read`, `upload_from_url`, `get_download_url`.
`page` keeps `attach_to_work_item`, `detach_from_work_item`, `list_work_item_pages`.
`member` spans four modules: `me` (`users.py`), `list_workspace` / `list_project`
(`workspaces.py`, `projects.py`), `list_roles` / `retrieve_role` (`roles.py`).

#### Small / unchanged

`work_log` (4), `work_item_property_value` (3), `work_item_activity` (2), `workspace` (2),
`pql` (1, unchanged).

---

## 5. Rollout

**Do not swap 177 tools out from under existing clients.** The OAuth redirect allowlist shows
live external consumers — Claude.ai, ChatGPT connectors, Cursor, VS Code — whose integrations
are pinned to current tool names.

Ship as a **parallel entrypoint**, not a replacement:

| Phase | Action | Breaking? |
|---|:---|:---|
| **0** | Lever 1 (optional-param defaults) applied in place | No |
| **1** | Add `plane_mcp/tools_v2/` + `get_stdio_mcp_v2()` / `http` route. Existing surface untouched. | No |
| **2** | Dogfood v2 internally; measure real `tools/list` payload; validate tool-selection accuracy | No |
| **3** | Default new client configs to v2; document migration | No |
| **4** | Deprecation notice on v1; remove after a full release cycle | Eventually |

Both surfaces call the same `plane_mcp/client.py` and the same `plane-sdk`. **No SDK changes at
any phase** — `tools/` is a pure pass-through layer and consolidation lives entirely above it.

### Suggested order of work (highest value first)

1. `intake` — 16% of the whole payload from 5 tools. Single best ROI.
2. `project` + `project_estimate` — 14%.
3. `work_item` + `work_item_property` — 15%, and the highest-traffic tools, so validate
   selection accuracy carefully here.
4. `initiative`, `cycle`, `module`, `customer*`, `release*` — bulk.
5. Everything else — mechanical.

---

## 6. Risks and open questions

**🔴 Does dropping `outputSchema` break clients?**
FastMCP returns `structuredContent` when a tool has an output schema. Dropping it means clients
get text content only. Must be verified against each real consumer in the redirect allowlist
before committing — this is the single blocking unknown, and it gates lever 2.

**🟠 Loss of schema-level required-field enforcement.**
`create_label` requires `name` today; merged with `delete`, `name` must go optional. Mitigated
by `_missing()` runtime validation + per-action docstrings, but it is a real trade: failures move
from schema-validation-time to runtime.

**🟠 Tool-selection accuracy.**
Picking among well-named flat tools is something models do reliably. Picking the right `action`
*plus* the right param subset is a different task. Needs measurement, not assumption — phase 2
should compare v1 vs v2 on a fixed set of representative prompts before defaulting anyone to v2.

**🟡 Loss of typed returns.**
`-> str` gives up Pydantic return models. Alternative worth pricing first: keep typed returns but
post-process the generated schema in the MCP layer (`$ref`-dedupe nested models, collapse
`anyOf`-null). Lower ceiling, but non-breaking and keeps structured output.

**🟡 Complementary lever not covered here: toolsets.**
FastMCP tags + a `PLANE_MCP_TOOLSETS` env var would let a deployment serve a subset. That solves
the hard client-cap problem directly and is far cheaper than consolidation. Worth doing
*regardless* of whether consolidation ships. Not mutually exclusive.

---

## 7. Reproducing these numbers

```bash
uv venv .venv && uv pip install -e . --python .venv/bin/python
```

```python
import asyncio, json, os
os.environ.update(PLANE_API_KEY="x", PLANE_WORKSPACE_SLUG="x")
from plane_mcp.server import get_stdio_mcp

async def main():
    tools = await get_stdio_mcp().list_tools()
    tot = out = 0
    for t in tools:
        dd = t.to_mcp_tool().model_dump(exclude_none=True)
        tot += len(json.dumps(dd, separators=(",", ":")))
        if dd.get("outputSchema"):
            out += len(json.dumps(dd["outputSchema"], separators=(",", ":")))
    print(f"tools={len(tools)} total={tot} (~{tot//4} tok) outputSchema={out} (~{out//4} tok)")

asyncio.run(main())
```

Chars→tokens uses a ÷4 approximation; treat all token figures as ±10%. Relative comparisons and
percentages are unaffected.

---

## 8. Spike results — intake module (measured)

Run on branch `spike/tool-consolidation-v2`; the surface now lives in `plane_mcp/tools_v2/`. Reproduce with
`.venv/bin/python benchmarks/measure_all.py` and `benchmarks/verify.py`.

Four variants of the intake module, to separate *consolidation* from *output-schema treatment*
— two levers §2 treated as one:

| Variant | Tools | Tokens | vs A | Breaking? |
|:---|---:|---:|---:|:---|
| **A** baseline (today) | 5 | 20,427 | — | — |
| **B** lossless schema compression only | 5 | 18,218 | −11% | **No** |
| **C** consolidated, typed union return | 1 | 10,119 | −51% | No |
| **BD** consolidated **+ compression** | 1 | **4,777** | **−77%** | **No** |
| **D** consolidated + `-> str` | 1 | **400** | **−98%** | **Yes** |

### Finding 1 — the two levers are multiplicative, not overlapping

Compression alone across all 177 tools is worth only **−11.4%** (125,649 → 111,295 tok), because
MCP gives each tool a *standalone* schema: there is no cross-tool `$defs`, so a model repeated in
40 tools cannot be deduped between them.

Consolidation fixes that. Once 5 tools become 1, the repeated models land in **one** schema where
`$ref` hoisting fires — compression jumps from −11% to −53% on that schema (C 10,119 → BD 4,777).

**Consolidation is what makes compression work.** §2's claim that levers 2 and 3 "overlap heavily"
is wrong; they compound.

### Finding 2 — we may not need to drop output schemas at all

**BD reaches −77% while keeping typed Pydantic returns and `structuredContent` intact.** That
retires the 🔴 risk in §6 as a *blocker*: the client-compat question becomes a question about the
last 21 points (BD → D), not about whether the project can proceed.

Recommended default is now **BD**, with D reserved for tools where structured output is
demonstrably unused.

### Correctness

`benchmarks/verify.py`, run against all 177 live tools:

```
compress() losslessness: 316/316 schemas round-trip exactly
  no failures — every $ref re-inlines to the collapsed original
nullable collapse shape: OK -> {"type":["string","null"],"default":null,...}
variant D validation paths: 5/5 OK (unknown action, missing project_id,
  missing work_item_id, status=0 without snoozed_till, status=2 without duplicate_to)
```

Two harness bugs were found and fixed during the spike, both of which had produced wrong numbers
on the first run — recorded here because they are easy to reintroduce:

- **Bottom-up `$ref` substitution inflates schemas.** Rewriting children first changes every
  ancestor's serialization, so no ancestor matches its tallied key; only leaf models dedupe while
  full-size bodies still land in `$defs`. Substitution must run **top-down**. First run reported
  BD as *larger* than C.
- **`tool.__doc__ = DOC` after `@mcp.tool()` is a no-op** — FastMCP captures the description at
  decoration time. Use `@mcp.tool(description=...)`. First run under-reported C and D by ~900 chars
  each by silently shipping empty descriptions.

### Live validation (real workspace)

The intake A/D equivalence run drove **both** surfaces through a real `FastMCP.Client` against a live
workspace and compares them — full `create → list → retrieve → update → delete` round trip on each.

```
A (5 current tools)   5/5 checks passed
D (1 consolidated)    5/5 checks passed
equivalence           4/4 (same counts, correct status, both cleaned up)
D error handling      2/2 (unknown action, missing work_item_id)
                     ------
                     16/16 checks passed, workspace swept clean
```

A pilot server composed all existing tools, minus the 5 intake tools, plus the consolidated one:

```
v2: 173 tools, 105,622 tok   (baseline 177 tools, 125,649 tok)
```

**One module swapped = −20,027 tok, 16% off the whole payload**, matching the §4 projection.

**Finding 3 — `-> str` does not remove `structuredContent`.** FastMCP still emits it for a
string-returning tool, as `{"result": "<json string>"}` — an opaque blob rather than typed data.
So the §6 🔴 risk is narrower than stated: clients that merely *check for* structured content keep
working; only clients that *parse typed fields out of it* would regress. Worth confirming per
client, but the failure mode is degradation, not absence.

### Caveat on extrapolation

Intake is the **best case**: 95% of its cost is output schema. Modules with a lower output-schema
share (`work_item_attachment` 7%, `milestone` 41%) will gain less from BD. The §4 projections were
built on the `-> str` assumption and are **not** revised for BD — that needs 2–3 more modules
measured across the output-share range before the full-server number can be trusted.

---

## 9. FULL-SURFACE RESULTS — all 29 modules implemented and measured

All 28 remaining modules were implemented (now `plane_mcp/tools_v2/`) and measured. This **supersedes the
projections in §4** with measured numbers.

```
A   baseline                 177 tools   502,596 ch  ~125,649 tok   [out 84,618 | in 17,004 | desc 18,629]
B   baseline + compression   177 tools   445,183 ch  ~111,295 tok   [out 72,374 | in 14,895 | desc 18,629]  -11.4%
C   v2 typed                  29 tools   239,134 ch  ~ 59,783 tok   [out 45,190 | in  5,864 | desc  7,840]  -52.4%
BD  v2 typed + compression    29 tools   168,975 ch  ~ 42,243 tok   [out 27,864 | in  5,650 | desc  7,840]  -66.4%
D   v2 str                    29 tools    63,348 ch  ~ 15,837 tok   [out  1,239 | in  5,864 | desc  7,840]  -87.4%
```

**The §4 projection was ~15,800 tok. Measured: 15,837.** Within 0.2%.

Two headline numbers, depending on the client-compat decision:

- **D (breaking): 125,649 -> 15,837 tok, −87.4%.** Gives up typed `structuredContent`.
- **BD (non-breaking): 125,649 -> 42,243 tok, −66.4%.** Keeps typed returns intact.

The BD figure is lower than the −77% intake-only result, exactly as §8's caveat predicted — intake
was the best case at 95% output-schema share. −66% is the honest full-surface number.

Input schema fell 17,004 -> 5,864 (−66%) and descriptions 18,629 -> 7,840 (−58%), confirming that
consolidation compounds with the schema work rather than overlapping it.

### Live validation — all 29 tools

`tests/test_tools_v2_live.py` calls one read action on every consolidated tool through a real
`FastMCP.Client` against the live workspace:

```
OK=22   FEAT(not enabled on this plan)=5   PRE(broken in baseline too)=1   SKIP(needs parent id)=1   FAIL=0
```

Zero failures attributable to consolidation. The five FEAT results are plan/licence gates
(`work_item_relation` -> HTTP 402, `initiative` -> feature disabled, `work_log` -> not enabled,
`project_estimate` -> no estimate exists, `work_item_property_value` -> 404).

**Pre-existing bug found (NOT caused by this work):** `list_work_item_activities` is broken against
`api.plane.so` today. The SDK types `PaginatedWorkItemActivityResponse.results[].epoch` as `int`,
but the API returns a float (`1785746693.7522993`), so pydantic raises `int_from_float`. Verified
by calling the **baseline** v1 tool and observing an identical failure. Worth its own ticket.

### Cross-module consistency issues to resolve before productionising

Six workers implemented in parallel from one brief; these divergences need a decision:

1. **Pagination envelope vs `.results`.** Mostly faithful to each source tool, but `release` and
   `release_tag` unwrap to `.results` where their sources returned the full envelope — losing
   `next_cursor`/`total_count`, so paging is no longer discoverable. The reference `intake.py` set
   this precedent. Fix: standardise on returning the envelope wherever the source did.
2. **`action` is now a reserved parameter name.** Four source tools had their own `action`
   parameter (`manage_release_work_items`, `manage_release_labels`, `manage_customer_work_items`,
   `manage_initiative_projects`). Workers independently renamed it to `operation` or `op` — pick one.
3. **`Literal` enums downgraded to `str`** in several places (`release.status`, `cycle.list.status`,
   `member.namespace`). Values moved into DOC; invalid input now fails at pydantic or a runtime
   guard rather than schema validation. Deliberate, but it is lost upfront validation.
4. **Tri-state booleans kept as `bool | None`** wherever `False` differs from unset
   (`is_active`, `is_prerelease`, `is_triage`, `is_default`, `is_multi`, …). Three workers reached
   this independently. Correct, and the main residual cost in the input schema.
5. **`manage_type_properties` now enforces a documented-but-unenforced precondition** (source
   silently returned `None` when both id lists were empty). Arguably a fix; still a behaviour change.
6. **Two modules import constants from `plane_mcp.tools`** despite the brief
   (`PQL_FULL_REFERENCE` in `work_item.py`; `_require_native_initiatives` in `initiative.py`), both
   to keep error text byte-identical. Fine for a spike; inline them for production.

### Known functional loss in variant D

`work_item_attachment.read` cannot return images under `-> str` — there is no image channel, so it
returns an error pointing at `get_download_url`. This is the first concrete case where D is not a
pure win and BD (or a per-tool exemption) is required.

### Correction to §1

An earlier draft said four modules use raw HTTP. Only **three** do
(`work_item_attachments.py` and the two auth providers) — and `work_item_attachments.py` uses
`requests`, not `httpx`. `customers/requests.py` was a false positive: the grep pattern `requests\.`
matched the SDK attribute `client.customers.requests.list(...)`.

---

## 10. ⚠️ Wire payload vs model-facing cost — this may invalidate the BD/D tradeoff

Everything in §1–§9 measures the **MCP wire payload** (server → client). That is not necessarily
what reaches the model.

**An Anthropic tool definition has no output-schema field.** It carries `name`, `description`,
`input_schema` (plus `strict`, `defer_loading`, `cache_control`, `allowed_callers`,
`eager_input_streaming`). Response-shape constraints live in `output_config.format` at the
*request* level, not per tool. An MCP client bridging `tools/list` into the Messages API therefore
has nowhere to put an `outputSchema` — it is most likely dropped.

If so, the model-facing numbers are:

| Variant | Full MCP payload | Model-facing (name + description + inputSchema) |
|:---|---:|---:|
| **A** baseline (177) | 125,649 | **40,474** |
| **C** v2 typed (29) | 59,783 | **14,481** |
| **BD** v2 typed + compression | 42,243 | **14,267** |
| **D** v2 str (29) | 15,837 | **14,481** |

**Spread across C / BD / D: 1.48%.** If output schemas never reach the model, the −87%-vs-−66%
tradeoff in §9 does not exist — BD and D are equivalent for context cost, and **BD wins outright**
because it is non-breaking and costs nothing extra.

Note the wire payload still matters independently: it is real bytes over stdio/HTTP and real client
memory, and it is what a client that *inlines* `outputSchema` into the description would pay.

### Settling it: `benchmarks/probe_model_tokens.py`

Needs an `ANTHROPIC_API_KEY` (or `ant auth login`). `count_tokens` is free; the whole probe is one
~20-token request plus free counts.

```
.venv/bin/pip install anthropic
.venv/bin/python benchmarks/probe_model_tokens.py
```

| | Question | Decides |
|---|:---|:---|
| **Q1** | Does a tool definition accept an output-schema field? | A 400 proves `outputSchema` cannot be forwarded — it is dropped |
| **Q2** | Real token cost of baseline vs v2, measured by the actual tokenizer | Replaces every `chars ÷ 4` figure in this document |
| **Q3** | Cost if a client inlines `outputSchema` into the description | The pessimistic upper bound |

Reading the result:

- **Q1 rejected/ignored + Q2 ≈ the model-facing column** → output schemas never reach the model.
  Choose **BD**; the breaking variant buys nothing.
- **Q1 counted, or your client inlines (Q3)** → the full payload is real and §9's tradeoff stands.

Verified by dry run with the API stubbed (`Q1` → 400 path, `Q2`/`Q3` tables render). Every token
figure in this document remains a `chars ÷ 4` approximation (±10% absolute; ratios unaffected)
until Q2 is run for real.

### Caveat on "14,267 tokens per query"

That is the **tool-definition block only**, and it is re-sent on **every** API call in a
conversation, not once per session. Total request input is `tools` → `system` → `messages`, so it
also carries the system prompt, the full conversation history, and the user turn. Because tool
definitions render *first*, they are the ideal prompt-cache prefix — written once at ~1.25×, read at
~0.1× thereafter — so with caching enabled the recurring cost is roughly a tenth of the cold-start
figure.

---

## 11. Housekeeping

`CLAUDE.md` line 61 currently reads *"29 tool modules … totaling 160+ tools."*
Actual: **33 modules, 177 tools**. Fix alongside this work.
