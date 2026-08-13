# Eval harness — runbook

Measures how well an LLM agent completes real Plane tasks through an MCP tool surface.
Every live task repetition uses a **live** Plane API with its own seeded fixtures and
teardown. Mutation tasks are verified by reading Plane back; read tasks match the final
answer against facts from the seed context or the API rather than trusting the agent's
claim that it succeeded.

The harness is agent-agnostic and surface-agnostic: any stdio MCP server can be measured
(`--server-cmd`), driven by any of five driver implementations. `DESIGN.md` explains why it is
built this way; this file is how to run it.

What you get per task: pass/fail, tool calls to done, which tools were picked (and whether
they were the optimal ones), response size and token-count provenance, errors, and the
agent's final text.

## Prerequisites

1. **A reachable Plane instance and API key.** Any reachable instance works, local or
   hosted. The key must be able to create and delete the catalog's project and
   workspace-scoped fixtures. Genuine plan or feature gates are recorded as skips where
   the fixture code handles them. Configure the harness with exactly these three values:

   ```bash
   export EVAL_PLANE_BASE_URL=https://your-plane.example.com
   export EVAL_PLANE_WORKSPACE_SLUG=your-workspace-slug
   export EVAL_PLANE_API_KEY=plane_api_your_key
   ```

2. **Model access for the driver you pick.** The API driver uses
   `ANTHROPIC_API_KEY` by default; OpenAI requires its SDK and `OPENAI_API_KEY`.
   CLI drivers require their corresponding local CLI to already be authenticated.

## Running

```bash
# Provider-neutral API loop (default provider: Anthropic)
.venv/bin/python -m evals --driver api --provider anthropic --model standard \
  --label local --out evals/output/api.jsonl

# Everything, one surface (free-form model IDs pass through to the CLI)
.venv/bin/python -m evals --driver codex-cli --model YOUR_CODEX_MODEL_ID \
  --label local --out evals/output/local.jsonl

# A few tasks while iterating
.venv/bin/python -m evals --driver codex-cli --model YOUR_CODEX_MODEL_ID \
  --label local --tasks W5,W8 --out evals/output/spot.jsonl

# Someone else's server (a PR branch, another repo) — "external mode"
.venv/bin/python -m evals --driver codex-cli --model YOUR_CODEX_MODEL_ID \
  --label their-pr --server-cmd "/path/to/their/.venv/bin/plane-mcp-server stdio" \
  --server-env KEY=VALUE --out evals/output/their-pr.jsonl
```

Useful flags: `--reps N` (repetitions per task), `--resume out.jsonl` (skip completed or
skipped `(task, rep, label)` keys and retry rows with recorded errors), `--list` / `--dry-run`
(no network).

**External mode** (`--server-cmd`) records `server: "external"`. Foreign tool names have no catalogued optimal/alternate sets,
so use success, call counts, and errors for those rows; their mispick values are not
comparable to catalogued surfaces.

### Drivers

| Driver | Backend | Notes |
|---|---|---|
| `api` | Owned API + MCP loop | Provider-neutral; tiers resolve for `--provider anthropic` (default) or `openai` |
| `codex-cli` | OpenAI Codex CLI | `standard` and `fast` resolve to verified GPT-5.6 IDs |
| `claude-cli` | Claude Code CLI | `standard` resolves to `sonnet`; `fast` resolves to `haiku` |
| `antigravity-cli` | Antigravity CLI (`agy`) | Verified against `agy models`; runs under a synthetic HOME so its MCP config is ours, not yours |
| `opencode-cli` | OpenCode | Tiers are intentionally unmapped; pass an explicit ID listed by `opencode models` |

### Model tiers

The harness has exactly two provider-neutral tiers: `standard`, the workhorse used for the
battery, and `fast`, the lower-cost option. Resolution is scoped to both driver and provider:

| Driver | Provider | `standard` | `fast` |
|---|---|---|---|
| `api` | Anthropic | `claude-sonnet-5` | `claude-haiku-4-5` |
| `api` | OpenAI | `gpt-5.6-sol` | `gpt-5.6-luna` |
| `claude-cli` | Anthropic | `sonnet` | `haiku` |
| `codex-cli` | OpenAI | `gpt-5.6-sol` | `gpt-5.6-luna` |
| `antigravity-cli` | Google | `gemini-3.6-flash-high` | `gemini-3.6-flash-low` |
| `opencode-cli` | Project-configured | **unmapped** | **unmapped** |

Only `standard` and `fast` are harness vocabulary. Every other `--model` value passes through
unchanged, including vendor aliases such as `sonnet` / `haiku` and full IDs such as
`claude-opus-5`, `gpt-5.6-sol`, or `openai/gpt-5.6-sol`. An unmapped tier fails before the run
and tells you to pass an explicit model ID; the harness never guesses one.

Result and meta rows keep `requested_model`, `requested_tier`, and `resolved_model` separately.
The compatibility field `model` remains the provider-reported model when available, otherwise
the resolved ID. This makes old readers continue to work while preserving which tier produced
the row even after its mapping changes.

Every CLI driver records the actual JSON-RPC traffic through a recording proxy, so tool
calls are normally counted from the wire rather than from whatever the agent claims it did.
If a sidecar is incomplete, the driver can fall back to its CLI event stream or transcript.

The API driver executes MCP calls itself, records exact result character counts, and sizes
result tokens without making a provider request per result. A backend may supply a token
counter; otherwise rows set `result_tokens_estimated: true` and use a deterministic
character-based estimate. CLI drivers use that same shared estimate from the result character
counts in the recording sidecar, so every driver reports response-token cost and estimated
counts are explicitly marked.

Exact CLI-side counting is opt-in with `--record-result-payloads`. It makes the proxy retain
the serialized tool-result text long enough for the harness to count it with a locally
importable tokenizer (`tiktoken`/`cl100k_base`); if that tokenizer is unavailable, the harness
falls back to the same marked estimate. The default stays **off** because payloads contain live
workspace data and bloat the sidecar. For comparing tool surfaces, the default chars-derived
estimate is monotonic in the thing being compared anyway. Do not enable payload recording by
habit; use it only when the more sensitive, larger sidecar is justified.

### Reading results

```bash
.venv/bin/python -m evals.report evals/output/local.jsonl                     # one surface
.venv/bin/python -m evals.report --table evals/output/*.jsonl                 # side by side
.venv/bin/python -m evals.report --table --markdown evals/output/*.jsonl      # for a PR
```

Rows are deduped latest-wins per `(task_id, rep, label)`, so a re-run of a single task
supersedes its earlier row in the same file. Skipped tasks are excluded from success
denominators, as are rows with recorded errors. Result-token columns use `~` for estimates,
`*` for mixed measured/estimated values, and `?` for legacy values whose provenance was not
recorded.

With `--reps N`, each `(task, rep)` is independently seeded, run, verified, and torn down.
Multi-rep reports show each task's pass count, Wilson interval, and whether its pass/fail
answer changed across completed repetitions. The measured noise-floor line converts those
flips into task-count units: if `U` tasks were unstable, surface differences of `U` tasks or
fewer should be treated as within observed run-to-run variance, making `U + 1` the minimum
meaningful difference from that sample. This is an empirical guardrail, not proof that larger
differences are statistically significant.

Every result row carries a `battery` fingerprint derived from the selected catalog's prompts
and tool metadata. Compare rows only when their fingerprints match: a table that mixes
fingerprints is comparing different questions, even when task IDs are the same. In particular,
results from a task whose output contract changed are not directly comparable with its rows in
older batteries. `evals.report --table` warns when its input rows span fingerprints.

## Running surfaces in parallel

Tasks that touch **workspace-scoped** fixtures (release tags, customer properties) collide
if two runs share a workspace. Give each concurrent run its own workspace:

```bash
EVAL_PLANE_WORKSPACE_SLUG=ws1 .venv/bin/python -m evals \
  --driver codex-cli --model YOUR_CODEX_MODEL_ID \
  --label local --out evals/output/local.jsonl &
EVAL_PLANE_WORKSPACE_SLUG=ws2 .venv/bin/python -m evals \
  --driver codex-cli --model YOUR_CODEX_MODEL_ID \
  --label their-pr --server-cmd "/path/to/their/.venv/bin/plane-mcp-server stdio" \
  --out evals/output/their-pr.jsonl &
wait
```

Keep the workspaces **empty apart from eval fixtures**. Unrelated projects and work items
in one workspace but not another skew every workspace-wide task in that column.

## Adding a task

Tasks live in the `evals/tasks/` package, grouped by task class and kept beside their
verifiers:

- `read.py`: R1-R7
- `write.py`: W1-W10
- `schema.py`: S1-S5
- `cross.py`: C1-C2
- `debias.py`: I1-I5 and L1-L5

Shared prompt, matcher, and API lookup machinery lives in `common.py`. `tasks/__init__.py`
assembles the class lists in the pinned catalog order and re-exports the public task API.
A task is a dict:

```python
{
    "id": "W11",
    "tags": {"write", "tier1"},
    "prompt": f"In project {{project}}, ...",  # {project} is bound at run time
    "optimal_calls": 3,
    "optimal_tools": {"list_cycles", "complete_cycle"},  # scored as optimal picks
    "alternate_tools": {"list_projects"},  # acceptable, not optimal
    "needs": {"items", "cycles"},  # fixtures to seed
    "verify": verify_w11,
}
```

`needs` tokens: `items`, `labels`, `bug_type`, `cycles`, `cycles_open_past`, `module`,
`intake`, `customer`, `release`, `activity_feed`, `second_project`,
`leave_cycles_worklogs_off`. Each task gets its own freshly seeded project, so fixture
variants (e.g. `cycles_open_past`) don't leak between tasks.

### Writing a verifier

Verifiers are `async def verify_x(plane, ctx, run) -> (ok: bool, note: str)`. Keep a new task
and its verifier in the same class module, add it to that module's exported task list, and
preserve the assembly order in `tasks/__init__.py`.

Mutation verifiers must read the resulting state through the Plane API. Read verifiers must
derive the expected facts from the API or seed context and match an explicit answer contract
instead of scanning free-form prose. Use exact `field: value` lines and the shared contract
matchers; for numeric answers, prefer a prompt such as `Answer with a line 'count: N'`. A
loose substring can make `4` match `24`, and prose matching can accidentally grade an agent's
writing habits instead of its answer.

**Check the shape the API actually returns.** Dates come back as timestamps
(`2026-08-12T00:00:00Z`), so comparing one to a bare `2026-08-12` silently never matches —
a verifier that can only fail is worse than no verifier. Have the test stub return the real
shape.

Then prove the verifier can fail:

```bash
.venv/bin/python -m evals --canary --label local
```

The canary seeds each task, calls its verifier with an **empty** agent
result, and exits non-zero if any verifier passes a do-nothing agent. Run it after touching
tasks, fixtures, or verifiers.

**Make the task achievable before blaming a surface.** For example, W6 declares the
`cycles_open_past` fixture variant because it asks the agent to close Sprint 12; the seeder
must not pre-close the cycle that the task is meant to change.

## Running a local Plane

Any reachable Plane works, so how you get one is your own setup and is not kept in this
repo. If you run plane-ee locally, two things make it usable for evals:

- Point `FEATURE_FLAG_SERVER_BASE_URL` at a flag server that answers every flag as on.
  The gated tasks (releases, customers, worklogs, work item types) need it, and the
  hosted flag server has them off for a local workspace.
- Raise `API_KEY_RATE_LIMIT`; a full battery makes far more API calls than the default
  allows.

Keep such scripts outside version control — `localdev/` is ignored for exactly this.

## Local gotchas

- If seeded comments do not materialize as activities, the activity-feed task self-skips
  with `env:no-activity-worker` rather than failing the agent.
- **Gated endpoints returning 402 on a workspace that should work.** Feature flags are
  cached per workspace, and the cache does not record which flag server answered. Any
  process that touches the DB while pointed at a *different* flag server than the running
  API — sourcing `plane-ee/apps/api/.env` gets you the remote one, where these flags are
  off — caches that answer for the workspace, and
  the API then serves the cached miss instead of asking its own mock. The tell is a canary
  that reports every verifier broken at once. Clear `ff:<slug>:*` and rotate
  `ff_ver:<slug>` (`plane.payment.flags.cache`) and the next request refetches. Observed
  while creating a workspace from a Django shell; a plan/licence problem looks identical
  from the outside, so check this first.
- A workspace licence is **not** required for local runs: the mock flag server enables
  every flag regardless, and an unlicensed workspace seeds all fixtures (verified by
  canary against a workspace with no licence row).
- **Offline tests** cover the harness itself and need no Plane instance:
  `env -u REDIS_HOST -u REDIS_PORT .venv/bin/python -m pytest -q --ignore=tests/test_integration.py`
