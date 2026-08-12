# Plane MCP Tool-Surface Eval Harness

Measures how well an LLM agent completes real Plane tasks through this MCP server's tool
surface. Produces decision-grade numbers for the tool-consolidation discussion: success rate,
tool calls to done, wrong-tool picks, and per-call response token cost.

This document is the full spec. Phase 1 (walking skeleton) implements a subset — see
"Phase 1 scope" at the bottom.

## Why

The live surface is 177 tools. A consolidation proposal (139→47) exists, but its cost
claims were estimate-based and wrong by 3.4× when measured. Before reshaping anything we
need empirical answers to:

1. **Mispick rate** — how often does an agent choose the wrong tool among overlapping ones
   (7 list-variants for work items, links vs relations, etc.)?
2. **Calls-to-done vs optimal** — how much does the name→UUID resolution dance and
   sub-object fan-out (item + comments + links as separate calls) cost?
3. **Response bloat** — how many tokens does each tool result actually inject into context?

The harness must support A/B comparison: same tasks against different tool surfaces
(`full` today; later `core` tag-filter and `v2` transform-layer variants).

## Architecture

**Driver:** the Anthropic Python SDK's beta tool runner with its MCP conversion helpers —
NOT the Claude Agent SDK, NOT a hand-rolled agent loop. This measures the MCP surface in
isolation (no coding-harness system prompt or built-in tools polluting the numbers).

The exact pattern (this is documented SDK API — do not improvise alternatives):

```python
from anthropic import AsyncAnthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

client = AsyncAnthropic()  # resolves ANTHROPIC_API_KEY / ant-auth profile from env

server_params = StdioServerParameters(
    command=sys.executable,
    args=["-m", "plane_mcp", "stdio"],
    env={
        "PLANE_API_KEY": os.environ["EVAL_PLANE_API_KEY"],
        "PLANE_WORKSPACE_SLUG": os.environ["EVAL_PLANE_WORKSPACE_SLUG"],
        "PLANE_BASE_URL": os.environ.get("EVAL_PLANE_BASE_URL", "https://api.plane.so"),
    },
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as mcp_client:
        await mcp_client.initialize()
        tools_result = await mcp_client.list_tools()
        runner = client.beta.messages.tool_runner(  # sync call — returns the runner
            model=MODEL_ID,
            max_tokens=8192,
            max_iterations=15,  # turn cap per task
            system=SYSTEM_PREAMBLE,
            messages=[{"role": "user", "content": task["prompt"]}],
            tools=[async_mcp_tool(t, mcp_client) for t in tools_result.tools],
        )
        async for message in runner:
            # capture tool_use blocks from message.content
            for block in message.content:
                if block.type == "tool_use":
                    record_call(block.name, block.input)
            # capture tool results (cached — tools still run exactly once)
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                record_results(tool_response)  # user message w/ tool_result blocks
            final = message
```

Notes:
- One fresh stdio server subprocess per task run (cheap, isolates state).
- Record `message.usage` from **every** yielded message (input_tokens, output_tokens,
  cache_read_input_tokens, cache_creation_input_tokens) — this is the exact context cost,
  returned free; the per-result counts below are a size proxy, not the cost figure.
- Record the final message's `stop_reason`, and whether the loop ended by exhausting
  `max_iterations` — a capped/truncated run must be distinguishable from a genuine failure.
  Detect the cap from `stop_reason` (the runner can legitimately finish with `end_turn` on
  exactly its last permitted iteration — an unconditional iteration-count check misreports
  that as capped).
- **Never call `generate_tool_call_response()` on a refusal-terminated message**
  (`stop_reason == "refusal"`): the SDK deliberately skips executing those tool_use blocks
  (side effects the model never confirmed), and calling it from the loop body bypasses that
  guard and fires real writes at the eval workspace.
- `wall_time_s` measures the agent loop only: start the clock after `list_tools()` returns,
  stop it when the loop exits — MCP subprocess spawn/teardown and post-loop token counting
  are excluded.
- Build the stdio server env **from scratch** (`PATH`, `HOME`, plus exactly the three
  `PLANE_*` vars) — never inherit `os.environ`. `plane_mcp/client.py` prefers
  `PLANE_INTERNAL_BASE_URL` over `PLANE_BASE_URL`, so an inherited value silently points
  the agent at a different Plane instance than seed/verify.
- A harness/API failure (SDK exception, MCP crash) is recorded as `error: "<msg>"` on the
  row — it is neither a task failure nor a skip, and the row's zeroed metrics must not
  enter any statistic.
- `SYSTEM_PREAMBLE` names the eval workspace slug and project name, states "complete the
  task using the available tools, then stop", and nothing else. Keep it under 100 words —
  it is part of the measured context.
- Omit `thinking` and sampling params entirely (adaptive thinking is the default on
  claude-sonnet-5; `temperature`/`top_p`/`top_k` are rejected).
- Final assistant text = the last yielded message's text blocks (used by read-task verifiers).

**Token counting of tool results:** use the API's count_tokens endpoint, never tiktoken
(wrong tokenizer for Claude, ~15-20% off):

```python
n = (
    await client.messages.count_tokens(
        model=MODEL_ID,
        messages=[{"role": "user", "content": result_text}],
    )
).input_tokens
```

Rules:
- Run these counts **after** the agent loop finishes, not inline — they must not pollute
  `wall_time_s`. Buffer the raw result strings during the run, count at the end.
- A tool_result's content may be a list of blocks. Concatenate the text of `text` blocks;
  for non-text blocks (e.g. image) record `result_kind: "image"` with `result_tokens: null`
  and `result_chars` of the raw payload. `is_error` results are counted like text.
- Also record raw `len(chars)` alongside every count.

**Models** (CLI aliases → IDs; these are deliberate, do not substitute):

| alias | model id | role |
|---|---|---|
| `sonnet` | `claude-sonnet-5` | default / representative agent |
| `haiku` | `claude-haiku-4-5` | canary — weaker models amplify tool-surface defects |

## Environment

| var | purpose |
|---|---|
| `ANTHROPIC_API_KEY` | driver LLM auth (or an `ant auth` profile) |
| `EVAL_PLANE_API_KEY` | Plane API key for the **dedicated eval workspace** |
| `EVAL_PLANE_WORKSPACE_SLUG` | eval workspace slug — never a production workspace |
| `EVAL_PLANE_BASE_URL` | optional, defaults to `https://api.plane.so` |

Seeding and verification talk to Plane directly via `plane-sdk` (already a dependency).
Construct the client the same way `plane_mcp/client.py` does for stdio mode, but from the
`EVAL_*` vars.

## Files

```
evals/
  __init__.py
  DESIGN.md      # this file
  tasks.py       # task definitions (plain dicts) + verifier functions
  seed.py        # per-run fixture create/teardown via plane-sdk
  run.py         # CLI driver (python -m evals.run)
  report.py      # summary table + A/B delta (python -m evals.report)
  results/       # *.jsonl output — gitignored
```

Dependencies: add to `pyproject.toml`:

```toml
[project.optional-dependencies]
evals = ["anthropic[mcp]>=<latest at implementation time>"]
```

Pin a `>=` floor at whatever the current anthropic release is when you implement, and
verify `from anthropic.lib.tools.mcp import async_mcp_tool` actually imports in the venv.
Do NOT change the existing `mcp==1.26.0` pin — `anthropic[mcp]` must coexist with it.
No other new dependencies. Stdlib only otherwise (argparse, json, asyncio, uuid, time).

## Task schema (`tasks.py`)

Plain dicts, no classes:

```python
{
    "id": "W1",
    "tags": {"write", "tier1"},
    "prompt": "Create a work item in project {project}: title 'Login page 500s on empty "
    "password', priority urgent, assign it to me, and add the 'auth' label.",
    "optimal_calls": 4,
    "optimal_tools": {"get_me", "list_projects", "list_labels", "create_work_item"},
    "alternate_tools": {
        "search_work_items",
        "list_states",
        "retrieve_project",
        "get_workspace_members",
        "manage_work_item_assignee",
        "manage_work_item_label",
        "update_work_item",
    },
    "needs": {"labels"},  # fixture groups seed.py must create
    "verify": verify_w1,  # async (plane, ctx, run) -> (bool, note)
}
```

- `{project}` in prompts is formatted with the seeded project name at runtime.
- `optimal_tools` and `alternate_tools` are **disjoint** sets. Every call is classified as
  one of `optimal` / `alternate` / `out_of_set`, plus an independent `is_error` flag.
  **Mispick = alternate + out_of_set** — this is the eval's headline metric, so authoring
  matters: a tool that works but is the *wrong pick among overlapping variants* (a list
  variant where search is optimal, a link where a relation is asked for) belongs in
  `alternate_tools` or nowhere, never in `optimal_tools`. The full ordered call list is
  kept in the JSONL so classifications can be re-derived offline if sets are revised.
- **Action-dispatch surfaces need a finer mispick unit** (added 2026-08-11, for the P2
  A/B that compares PR #195's 29-tool `action`-multiplexer variant): on such a surface
  the model almost always picks the "right" *tool* and fails inside it — wrong `action`,
  or params invalid for the chosen action. Tool-name classification alone would
  under-count exactly that failure mode and bias the A/B toward consolidation. Rule: when
  a surface under test multiplexes verbs through a parameter, the classification unit is
  `(tool, action)` — task authors list optimal/alternate *(tool, action)* pairs — and a
  schema-valid call whose params are invalid for its declared action counts as
  `out_of_set`, not merely `is_error`. Flat surfaces are unaffected (their action unit is
  the tool name). The stored ordered call list already carries arguments, so this scoring
  can also be re-derived retroactively.
- `verify` receives `run = {"final_text": str, "calls": [...]}` alongside the plane client
  and seed ctx. Write tasks assert end state through the Plane API; read tasks match the
  final text against seeded facts using **word-boundary regexes on exact seeded values**
  (each verifier states its matching rule in a comment — naive substring matching is a
  known false-positive source, e.g. bare "4" inside "24"). Resolve expected values via
  API at verify time — never hardcode sequence numbers or UUIDs.

## Fixtures (`seed.py`)

Per run: create project named `EVAL {run8}` (`run8` = first 8 hex chars of `uuid4().hex`;
identifier `EV` + 4 of those hex chars uppercased, ≤12 chars) in the eval workspace.
Unique-per-run naming makes runs parallel-safe and crash-visible. Provide
`seed(plane, run_id, needs) -> ctx` and `teardown(plane, ctx)`; `ctx` carries project_id,
project name, and IDs of everything seeded.

`seed()` must guarantee teardown information even on partial failure: it mutates a
caller-provided ctx in place (or raises with the partial ctx attached), so a failure
after project creation never leaks an untracked project. Feature probes must read the
keys the API actually returns (workspace toggle: `is_work_item_types_enabled`) and a
seed failure must fail loudly — never masquerade as a plan-gate skip.

The R1 target item is seeded into a **non-default state** (e.g. a `started`-group state)
so a guessed default state name cannot pass verification.

Teardown deletes the project **plus every workspace-scoped object seeded** — customers
(and any other object that survives project deletion) are tracked in `ctx` and deleted by
ID explicitly; project deletion alone is not sufficient cleanup.

Seed only the fixture groups the selected tasks declare in `needs`:

| group | contents |
|---|---|
| `items` | ~12 work items with fixed titles/priorities incl. "Payment webhook drops retries" (urgent); exactly 4 urgent open items total |
| `labels` | labels `auth`, `triage`, `perf` |
| `cycles` | "Sprint 12" (past-dated), "Sprint 13" (current) |
| `module` | "Checkout revamp" with 3 completed items |
| `bug_type` | work item type "Bug" — **plan-gated feature**: if the API rejects creation, seed() records `bug_type: None` and dependent tasks are SKIPPED (recorded in JSONL with `"skipped": reason`), not failed |
| `intake` | 2 intake items (one billing request, one obvious spam) |
| `customer` | customer "Acme Corp" + request "SSO support" |
| `release` | release "1.2.0" with 2 changelog entries |

## Runner CLI (`run.py`)

```
python -m evals.run --tasks R1,W1,S1 --model sonnet --reps 1 \
    --surface full --out evals/results/<run_id>.jsonl
python -m evals.run --list          # print task table, no network
python -m evals.run --dry-run --tasks R1   # print resolved prompt + seed plan, no network
```

- `--surface` is recorded in the JSONL and (for now) only `full` is implemented; it is the
  future hook for tag-filtered/transformed variants. Unknown values error.
- `--reps N` repeats each task N times (fresh seed + fresh server per rep).
- Per task-rep flow: seed → run agent → verify → append JSONL row → teardown (teardown in
  a `finally`; on crash, print the orphaned project name).

One JSONL row per task-rep:

```json
{"run_id": "...", "ts": "...", "git_sha": "...", "surface": "full",
 "model": "claude-sonnet-5", "task_id": "W1", "rep": 0,
 "success": true, "verify_note": "...", "skipped": null, "error": null,
 "stop_reason": "end_turn", "hit_max_iterations": false,
 "calls": [{"tool": "list_projects", "class": "optimal", "args_chars": 42,
            "result_tokens": 830, "result_chars": 3120, "result_kind": "text",
            "is_error": false}],
 "num_calls": 4, "errored_calls": 0, "alternate_calls": 0, "out_of_set_calls": 0,
 "total_result_tokens": 2210,
 "usage_per_iteration": [{"in": 38210, "out": 412, "cache_read": 36100, "cache_write": 0}],
 "cum_input_tokens": 152840, "wall_time_s": 31.4}
```

## Report (`report.py`)

```
python -m evals.report evals/results/A.jsonl [evals/results/B.jsonl]
```

Single file, per task: `n`, success as `k/n` with a 95% Wilson interval, median calls
(with IQR) vs optimal, mispick rate (alternate + out_of_set over total calls), errored
calls, capped runs (`hit_max_iterations` or `stop_reason == "max_tokens"`) and harness-error
rows (`error != null`) each reported as their own column — never silently folded into
failures, and error rows excluded from success/medians entirely — median & p95 result_tokens per
call, and median `cum_input_tokens`.

Two files: same table with per-task deltas (B − A). **Refuse the delta mode (exit with a
message) when either file has n < 5 for any shared task** — comparative claims below that
floor are noise. Plain text, stdlib only.

Optimal-path caveats discovered during review (bake into the sets and a comment):
- **R1**: `search_work_items` returns no state field (`WorkItemSearchItem` has only
  name/id/sequence_id/identifiers). The true 1-call path is `list_work_items` (WorkItem
  carries `state: str | StateLite`; `expand=state` yields the name). search is alternate.
- **S1**: `create_work_item_property` accepts inline `options`, so the optimal path is
  3 calls (`list_projects` → `resolve_work_item_type` → `create_work_item_property`) —
  separate option-creation / list_work_item_types calls are alternates, not optimal.

## Full task list (target: 20 tasks)

Defined in the consolidation analysis; implement incrementally. IDs are stable.

| id | prompt (abbrev) | probes | optimal |
|---|---|---|---|
| R1 | state of item titled 'Payment webhook drops retries' | list vs search (search has no state) | 1 (`list_work_items`) |
| R2 | how many urgent open items | count/list/search pick, UUID dance | 1–2 |
| R3 | items assigned to me due this week | assignee resolution | 2 |
| R4 | what's in the active cycle, anything overdue | PQL activeCycle() discovery | 1–2 |
| R5 | summarize discussion on a known item | sub-object fan-out | 2 |
| R6 | which project has more open bugs (needs 2nd project) | cross-project composition | 2–3 |
| W1 | file a bug w/ priority+assignee+label | lookup overhead before create | 3–4 |
| W2 | move item to Done | state name→UUID | 2–3 |
| W3 | comment on an item | happy path baseline | 2 |
| W4 | rename label triage→needs-triage | update_label pick | 2 |
| W5 | archive all completed items in module | no-bulk N-call burn | 2+N |
| W6 | move unfinished items Sprint 12→13, close Sprint 12 | transfer+complete workflow | 3–4 |
| W7 | mark A blocking B + add reference URL | relations-vs-links confusion | 3 |
| W8 | log 2h on an item for yesterday | worklog | 2 |
| S1 | add Severity dropdown (Critical/Major/Minor) to Bug type | property + inline options | 3 |
| S2 | add Fibonacci estimate scale, set item to 5 pts | estimates chain | 4–5 |
| S3 | create type Incident w/ required text property | type+property+attach | 4–5 |
| S4 | triage intake: accept billing, reject spam | workflow vs endpoint tools | 3–4 |
| C1 | create customer Acme, link request to item | customers domain | 3–4 |
| C2 | what shipped in release 1.2.0 | releases/changelog | 1–2 |

## Phase 1 scope (walking skeleton)

Implement end to end, nothing more:

1. `tasks.py` with **R1, W1, S1 only** (verifiers included).
2. `seed.py` covering fixture groups `items`, `labels`, `bug_type`.
3. `run.py` with `--list`, `--dry-run`, and the full live path (seed→run→verify→teardown).
4. `report.py` single-file mode (A/B delta mode can be a stub that errors clearly).
5. `pyproject.toml` evals extra + `evals/results/` gitignored.

Constraints:
- Python 3.10+, ruff clean (`ruff format evals/ && ruff check evals/` — line length 120,
  rules E,F,I,UP,B per pyproject).
- Match the codebase's existing style; no classes where dicts do, no framework.
- **No live credentials exist in this checkout** — done means: `--list` and `--dry-run`
  work without network, imports resolve in a fresh venv after
  `uv pip install -e ".[dev,evals]"`, ruff passes. The live path must be complete and
  plausible but cannot be executed yet.
- Do NOT commit. Do NOT touch `.ccwrc`, `.env*`, or anything under `plane_mcp/`.
