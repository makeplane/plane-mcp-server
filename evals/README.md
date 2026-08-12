# Eval harness — runbook

Measures how well an LLM agent completes real Plane tasks through an MCP tool surface.
Every task runs against a **live** Plane API with fixtures seeded and torn down per run,
and is graded by a verifier that reads the API back — not by inspecting the agent's prose.

The harness is agent-agnostic and surface-agnostic: any stdio MCP server can be measured
(`--server-cmd`), driven by any of five agent backends. `DESIGN.md` explains why it is
built this way; this file is how to run it.

What you get per task: pass/fail, tool calls to done, which tools were picked (and whether
they were the optimal ones), errors, and the agent's final text.

## Prerequisites

1. **A local Plane API.** `evals/env.sh` starts plane-ee on `:8000` plus a mock
   feature-flag server on `:9911` that turns every flag on.

   ```bash
   export PLANE_EE_API_DIR=/path/to/plane-ee/apps/api
   export PLANE_EE_VENV=/path/to/plane-ee-venv
   evals/env.sh up       # down | status
   ```

2. **A workspace and an API key** on that instance, with a Business/Enterprise license so
   the gated fixtures (customers, releases) can be seeded.

   ```bash
   export EVAL_PLANE_BASE_URL=http://localhost:8000
   export EVAL_PLANE_WORKSPACE_SLUG=<slug>
   export EVAL_PLANE_API_KEY=plane_api_...
   unset REDIS_HOST REDIS_PORT      # else the SDK client picks up a stale cache config
   ```

3. **Model access for the driver you pick.** The API driver uses
   `ANTHROPIC_API_KEY` by default; OpenAI requires its SDK and `OPENAI_API_KEY`.
   CLI drivers require their corresponding local CLI to already be authenticated.

## Running

```bash
# Provider-neutral API loop (default provider: Anthropic)
.venv/bin/python -m evals.run --driver api --provider anthropic --model sonnet \
  --surface full --out results/api.jsonl

# Everything, one surface
.venv/bin/python -m evals.run --driver codex-cli --model gpt-5.6-sol \
  --surface full --out results/legacy.jsonl

# A few tasks while iterating
.venv/bin/python -m evals.run --driver codex-cli --model gpt-5.6-sol \
  --surface full --tasks W5,W8 --out results/spot.jsonl

# Someone else's server (a PR branch, another repo) — "external mode"
.venv/bin/python -m evals.run --driver codex-cli --model gpt-5.6-sol \
  --surface their-pr --server-cmd "/path/to/their/.venv/bin/plane-mcp-server stdio" \
  --server-env PLANE_MCP_TOOLS_VERSION=v2 --out results/their-pr.jsonl
```

Useful flags: `--reps N` (repetitions per task), `--resume out.jsonl` (skip completed
`(task, rep)` pairs, retry only infra failures), `--list` / `--dry-run` (no network).

**External mode** (`--server-cmd`) runs every task with no surface-based skips, and turns
off mispick classification — foreign tool names have no optimal/alternate sets to score
against, so call *counts* stay comparable but "mispicks" reads `n/a`.

**On surfaces.** `--surface` without `--server-cmd` runs this repo's own server, and passes
the surface through as `PLANE_MCP_SURFACE`. This branch's server serves one surface, so
only `full` is real here: `v2` / `v2-schema` set an env var nothing reads, and you would
get legacy results labelled as something else. The task catalog keeps its per-surface
overlays (`surface_tools`) so those surfaces score correctly if the server ever grows them.
**Measure any other surface through `--server-cmd`** — that is how the PR surfaces were
compared, and it is honest about what it ran because it launches the server you name.

### Drivers

| Driver | Backend | Notes |
|---|---|---|
| `api` | Owned API + MCP loop | Provider-neutral; `--provider anthropic` (default) or `openai` |
| `sdk` | Alias for `api` | Retained for old commands/result pipelines |
| `codex-cli` | OpenAI Codex CLI | Pass a real model id (`gpt-5.6-sol`); the short-alias table is incomplete |
| `claude-cli` | Claude Code CLI | `--model sonnet` / `haiku` |
| `antigravity-cli` | Antigravity CLI (`agy`) | Runs under a synthetic HOME so its MCP config is ours, not yours |
| `opencode-cli` | opencode | Temp project config per run |

Every CLI driver records the actual JSON-RPC traffic through a recording proxy, so tool
calls are counted from the wire rather than from whatever the agent claims it did.

The API driver executes MCP calls itself, records exact result character counts, and sizes
result tokens without making a provider request per result. A backend may supply a token
counter; otherwise rows set `result_tokens_estimated: true` and use a deterministic
character-based estimate. CLI drivers retain null result-token counts because they do not
always expose complete tool result text.

### Reading results

```bash
.venv/bin/python -m evals.report results/legacy.jsonl                    # one surface
.venv/bin/python -m evals.report --table results/*.jsonl                 # side by side
.venv/bin/python -m evals.report --table --markdown results/*.jsonl      # for a PR
```

Rows are deduped latest-wins per `(task_id, rep, surface)`, so a re-run of a single task
supersedes its earlier row in the same file. Skipped tasks are excluded from success
denominators — a surface that cannot do something is not punished as a failure, it is
reported as a skip.

## Running surfaces in parallel

Tasks that touch **workspace-scoped** fixtures (release tags, customer properties) collide
if two runs share a workspace. Give each concurrent run its own workspace:

```bash
EVAL_PLANE_WORKSPACE_SLUG=ws1 ... --surface full      --out results/legacy.jsonl &
EVAL_PLANE_WORKSPACE_SLUG=ws2 ... --surface their-pr  --out results/their-pr.jsonl &
wait
```

Keep the workspaces **empty apart from eval fixtures**. Unrelated projects and work items
in one workspace but not another skew every workspace-wide task in that column.

## Adding a task

Tasks live in `evals/tasks.py`. A task is a dict:

```python
{
    "id": "W11",
    "tags": {"write", "tier1"},
    "prompt": f"In project {{project}}, ...",  # {project} is bound at run time
    "optimal_calls": 3,
    "optimal_tools": {"list_cycles", "complete_cycle"},  # scored as optimal picks
    "alternate_tools": {"list_projects"},  # acceptable, not optimal
    "surface_tools": {"v2": {"optimal_tools": {"close_cycle"}}},  # per-surface overlay
    "needs": {"items", "cycles"},  # fixtures to seed
    "verify": verify_w11,
}
```

`needs` tokens: `items`, `labels`, `bug_type`, `cycles`, `cycles_open_past`, `module`,
`intake`, `customer`, `release`, `activity_feed`, `second_project`,
`leave_cycles_worklogs_off`. Each task gets its own freshly seeded project, so fixture
variants (e.g. `cycles_open_past`) don't leak between tasks.

A `surface_tools` overlay may set `"expected_skip": True` to declare that a surface
genuinely cannot do the task. That is reported as a capability gap, not a failure.

### Writing a verifier

Verifiers are `async def verify_x(plane, ctx, run) -> (ok: bool, note: str)` and must read
state back through the API. Two rules, both learned the hard way:

**Never parse natural language.** Constrain the output in the prompt instead — end the
prompt with `Answer with a line 'count: N'` and match that line exactly. Regex over prose
produces both false passes ("10 attachments" satisfying a truth of 0) and false failures
("three" for 3), and no amount of tuning fixes it.

**Check the shape the API actually returns.** Dates come back as timestamps
(`2026-08-12T00:00:00Z`), so comparing one to a bare `2026-08-12` silently never matches —
a verifier that can only fail is worse than no verifier. Have the test stub return the real
shape.

Then prove the verifier can fail:

```bash
.venv/bin/python -m evals.run --canary --surface full
```

The canary seeds every task, calls each verifier with an **empty** agent result, and exits
non-zero if any verifier passes a do-nothing agent. Run it after touching tasks, fixtures,
or verifiers.

**Make the task achievable before blaming a surface.** A fixture that forbids what the
prompt asks turns the task into a coin flip on tool choice and will implicate whichever
server happens to pick differently. W6 was pre-closing a cycle the agent was asked to
close; it took two full runs to notice, because a side effect of an unrelated call was
tripping the verifier.

## Local gotchas

- **Comment activity never appears** without a running activity worker, so the tasks that
  read the activity feed self-skip (`env:no-activity-worker`) rather than fail.
- **A feature-flag cache poisoned by the wrong disco.** Anything that talks to the DB while
  sourcing `plane-ee/apps/api/.env` uses the *remote* flag server (all flags off) and
  caches that answer for a workspace the API server would otherwise serve from the local
  mock. Symptom: gated endpoints 402 and the canary reports every verifier broken. Fix:
  clear `ff:<slug>:*` and rotate `ff_ver:<slug>` (`plane.payment.flags.cache`).
- **Offline tests** cover the harness itself and need no Plane instance:
  `env -u REDIS_HOST -u REDIS_PORT .venv/bin/python -m pytest -q --ignore=tests/test_integration.py`
