# Eval harness — runbook

Measures how well an LLM agent completes real Plane tasks through an MCP tool surface.
Every live task repetition uses a **live** Plane API with its own seeded fixtures and
teardown. Mutation tasks are verified by reading Plane back; read tasks match the final
answer against facts from the seed context or the API rather than trusting the agent's
claim that it succeeded.

The harness is agent-agnostic and surface-agnostic: any stdio MCP server can be measured
(`--server-cmd`), driven by any of five driver implementations. `DESIGN.md` explains why it is
built this way; this file is how to run it.

What you get per task: pass/fail, observed calls to done, core and variable tool use across
successful repetitions, response size and token-count provenance, errors, and the agent's
final text.

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

Useful flags: `--reps N` (repetitions per task), `--resume out.jsonl` (skip completed
`(task, rep, label)` keys and plan-gated skips; retry recorded errors, cleanup failures,
fixture-collision skips, and unknown skips), `--list` / `--dry-run` (no network).

**External mode** (`--server-cmd`) records `server: "external"`. Success, call counts,
errors, and observed tool distributions use the same rules as local-server rows.

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

Read-task provenance is stricter than “a call happened.” Seeders place a hidden per-run
sentinel on the target entity, and the API driver or CLI proxy records only whether a
successful response exposed it **and its request targeted that seeded entity ID**. CLI
proxies receive only target IDs and one-way value fingerprints through a private one-shot
file; the raw sentinel is absent even if that file is inspected. The proxy unlinks it before
Plane MCP starts. Result rows contain the matched label, never the sentinel value or response body. Thus an unrelated
successful response cannot satisfy provenance; unavailable or incomplete matching is
diagnosed and fails closed.

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

Reports keep three verdicts separate: model success among evaluated rows, execution coverage
(evaluated rows / expected rows, including skipped task IDs and capability reasons), and run
completeness. A plan-gated-only run can therefore be **RUN COMPLETE** below 100% execution
coverage. The live runner and report commands exit 0 when the evaluation completed cleanly;
exit 0 does **not** mean the agent passed. Callers that need a pass-threshold exit must apply
that as a separate opt-in policy rather than overloading the completeness status.

With `--reps N`, each `(task, rep)` is independently seeded, run, verified, and torn down.
Multi-rep reports show each task's pass count, Wilson interval, and whether its pass/fail
answer changed across completed repetitions. Instability remains descriptive; it is not
converted into an ad-hoc threshold for declaring surface differences meaningful. Two-file
A/B reports instead pair shared tasks, report a paired-bootstrap 95% interval for the mean
per-task success-rate difference, and use a paired sign-flip permutation test for mean call
deltas. Zero call-delta ties remain in that paired sample. The inference treats tasks as
independent sampling units and assumes comparable task instances under both labels, so the
printed paired task count—and the resulting wide interval for small samples—matters.

Every result row carries a `battery` fingerprint derived from the selected catalog's task IDs,
prompts, and catalog revision, plus a `task_fingerprint` over that row's task ID, prompt, and
fixture names. The battery contains exactly what the agent is asked and no expectation about
how the answer should be produced. All report paths refuse with exit 2 before printing
measurements when persisted battery identities differ, including mixed rows within one file.

Canonical model, provider, driver, and server differences are comparison treatments. Declare
each intentional difference with `--vary`, for example `--vary resolved_model` or
`--vary provider,resolved_model`; any undeclared difference also refuses. Battery cannot be
declared as varying. Requested tier/model names remain provenance rather than canonical
identity, and the provider-reported realized model is printed as evidence instead of being
mechanically equated with the configured model.

The hash excludes fixtures and verifier bodies. `CATALOG_REVISION` in `tasks/catalog.py`
closes that gap: bump it whenever an excluded change redefines what a task asks, and explain
the comparison consequence in its docstring.

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
- `write.py`: W1-W11
- `schema.py`: S1-S5
- `cross.py`: C1-C2
- `debias.py`: I1-I5 and L1-L5

Prompt binding, answer matching, Plane lookups, and the skip signal live in `prompts.py`,
`answers.py`, `lookups.py`, and `skip.py`. `catalog.py` assembles the class lists in the
pinned catalog order, while `tasks/__init__.py` re-exports the public task API.
A task is a dict:

```python
{
    "id": "W11",
    "tags": {"write", "tier1"},
    "prompt": f"In project {{project}}, ...",  # {project} is bound at run time
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
preserve the assembly order in `tasks/catalog.py`.

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
# CI capability contract: these ids must be eligible and verified.
.venv/bin/python -m evals --canary --canary-strict R1,R2,W8 --label local
```

The canary seeds each task, calls its verifier with both an **empty** agent result and
plausible zero-call canned contract answers, then reports verified, skipped, and errored
task ids separately. It exits non-zero for a false pass, verifier/teardown error, zero
verified tasks, or a skipped id named by `--canary-strict`. Plan-gated skips outside that
explicit strict set remain allowed. Run it after touching tasks, fixtures, or verifiers.

**Make the task achievable before blaming a surface.** For example, W6 declares the
`cycles_open_past` fixture variant because it asks the agent to close Sprint 12; the seeder
must not pre-close the cycle that the task is meant to change.

## Running a local Plane

Any reachable Plane works, so how you get one is your own setup and is not kept in this
repo. If you run plane-ee locally, two things make it usable for evals:

- Raise `API_KEY_RATE_LIMIT`; a full battery makes far more API calls than the default
  allows.
- Optionally point `FEATURE_FLAG_SERVER_BASE_URL` at a flag server that answers every
  flag as on. This is **not** required. A capability the plan excludes makes its task
  record `env:plan-gated:<feature>` and drop out of the denominator, the same way L2
  handles a missing activity worker; the rest of the battery runs unaffected. Pointing at
  a permissive flag server simply means those tasks are measured rather than skipped.

  Which tasks that covers: C2 (releases), L4 (customers), R6 and S1 (work item types).

Keep such scripts outside version control — `localdev/` is ignored for exactly this.

## Local gotchas

- If seeded comments do not materialize as activities, the activity-feed task self-skips
  with `env:no-activity-worker` rather than failing the agent.
- A reviewed capability the workspace's plan excludes self-skips with
  `env:plan-gated:<feature>`. The closed allowlist is `customers`, `releases`,
  `work-item-types`, `initiatives`, and `teamspaces`; a typo or new name is unexpected
  until its real gate site is reviewed and the allowlist is deliberately extended.
  Only a refusal that names a plan limit counts: 402, or 403/400 whose body says so. A
  bare 403 is an ordinary permission denial and stays a real error, because classifying it
  as a gate would let a permission bug leave the denominator and read as "nothing to see".
- Run completeness uses an explicit skip taxonomy: known capabilities the environment does
  not provide (an allowlisted `env:plan-gated:<feature>` or the exact reason
  `env:no-activity-worker`) are expected skips.
  The task/capability pair must also match the task's declared fixture needs: for example,
  `env:plan-gated:customers` is expected for L4 but unexpected for W1 or C1.
  They reduce **EXECUTION COVERAGE** but do not break **RUN COMPLETE**. A dirty environment
  (`env:fixture-collision:*`) and every unrecognised reason are unexpected and make the run
  incomplete; there is intentionally no catch-all for new `env:*` reasons.
- New result headers declare the exact task-id subset and repetition count. Completeness
  compares raw `(task_id, rep)` occurrences with that declaration before latest-wins
  deduplication, naming missing and unexpected keys (including duplicate excess).
- Report headlines use a task-cluster bootstrap interval; the pooled repetition rate and
  Wilson interval remain visible but are explicitly labeled as pooled. A/B and surface-table
  reports warn when any input lacks a tool-manifest fingerprint.
- A feature switched **off for a project** is not a plan gate — it is configuration the
  harness sets itself, and W11 exists to measure what an agent does when it meets one.
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
- A workspace licence is **not** required for local runs. With a permissive flag server an
  unlicensed workspace seeds every fixture (verified by canary against a workspace with no
  licence row); without one, the gated tasks skip and the rest still run.
- **Offline tests** cover the harness itself and need no Plane instance:
  `env -u REDIS_HOST -u REDIS_PORT .venv/bin/python -m pytest -q --ignore=tests/test_integration.py`
