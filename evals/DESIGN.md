# Plane MCP Tool-Surface Eval Harness

This harness measures how well an LLM agent completes real Plane tasks through an MCP
tool surface. It exists to replace predictions about a surface with observations from
actual agent runs: whether the task succeeded, how many Plane calls it took, which tools
were selected, and how much tool-result content was returned to the model.

This document explains why the harness is shaped this way. Operational commands live in
`evals/README.md`.

## The questions it answers

The original tool-consolidation question breaks down into four measurable questions:

1. **Task success** — did the agent produce the verified Plane state or exact answer?
2. **Calls to done** — how much lookup, name-to-ID resolution, and sub-object fan-out did
   successful repetitions actually require?
3. **Tool-use stability** — which tools were core across successful repetitions, and where
   did repetitions choose different routes?
4. **Response bloat** — how much tool-result content was injected into the conversation?

Success is the guardrail around the other three. A surface that uses fewer calls or returns
less text but fails the task is not an improvement. Conversely, success rate alone hides
extra calls, variable routes, and large responses. The harness therefore records all four
dimensions for the same task execution.

The point is empirical comparison. Given the same task battery and declared treatment
dimensions, different surfaces can be compared from observed behavior rather than from tool
counts, schema inspection, or projected costs. Report identity validation refuses incompatible
batteries and undeclared canonical-identity differences before printing measurements. The
battery fingerprint records the selected task universe; per-task fingerprints preserve the
task-local payload needed for possible future intersection comparisons.

## What is measured

### Success

Each task has an asynchronous verifier. Mutation tasks read Plane back through the API and
check the resulting state. Read tasks compare the final assistant text with facts obtained
from the seeded context or resolved through the API, using explicit answer contracts and
exact-value matchers where the task defines them.

This avoids using the agent's explanation, confidence, or self-reported completion as the
source of truth. The model is also not asked to grade another model. Verification is tied to
the fixture and the Plane state the task was meant to affect. The canary reports which
verifiers were exercised, skipped, or errored and probes eligible verifiers with an empty
result plus plausible zero-call contract answers. CI can name an explicit strict set of task
ids that must be eligible in its environment.

Skipped tasks and infrastructure failures are recorded separately. The report excludes
both from success denominators; a plan gate, unavailable fixture, provider failure, or MCP
process failure is not rewritten as an agent task failure.

Caught exceptions follow one validity convention: continuing is allowed only when a local
fallback makes the result equivalent, and that catch documents why. Failures that can alter
the evaluated state, recorded evidence, cleanup, or report denominator are represented as
infrastructure, harness, or cleanup errors so run completeness cannot silently remain green.

### Calls to done

`num_calls` counts Plane MCP calls made during the task. The report shows the observed
distribution rather than assuming one run is representative. Every reported call-count
minimum, median, maximum, and Q1–Q3 span is conditioned on successful repetitions; a run
that failed early is not treated as a cost-to-success observation. There is no
author-declared call floor.

Two-label reports pair tasks before making inferential comparisons. Their success-rate
difference uses a paired percentile-bootstrap interval that resamples tasks as the
independent units. Their mean call-count delta uses a paired sign-flip permutation test on
the actual magnitudes, retaining zero-delta ties. These procedures assume comparable task
instances under the two labels, independent tasks, and exchangeable A/B labels under the
permutation null; they do not account for shared environment drift or dependence between
tasks. The report prints the paired task count so small samples remain visible.

Errored calls use that same successful, trace-intact row population. Within each task, the
absolute measure is the median errored-call count per repetition (parallel to the call-count
median), while the rate is the task's errored calls divided by its total calls. Cross-task
headlines average task values and paired intervals resample whole task deltas; they do not pool
calls across tasks. Reports retain task IDs for non-zero errors and print measured zeros. A
zero-attempt task has an undefined rate rather than an invented zero rate. `is_error` is the
MCP-level error flag: it counts all tool-reported failures, including an error that is the
correct outcome, and cannot detect an agent that successfully calls the wrong tool.

Single-run success headlines use the same sampling unit: each evaluated task contributes
its repetition success rate, and a deterministic cluster bootstrap resamples whole tasks.
The pooled repetition rate and its Wilson interval remain visible as a descriptive figure,
but are labeled pooled rather than presented as the headline confidence interval.

Client-local tools such as shell or tool-search helpers are retained separately as
`client_tool_calls`; they do not count as Plane calls. For an external server launched with
`--server-cmd`, the runner marks the row server as `external`; call counts and observed tool
distributions use the same rules as local-server rows.

### Observed tool distribution

The former author-declared optimal/alternate sets and mispick score were removed. Reports
now describe the tools agents used in successful repetitions:

- `tool_rep_frequency` is the share of successful repetitions that used each tool at least
  once. Repeated calls in one repetition count once for frequency.
- `tool_call_counts` is the total number of calls to each tool across those repetitions.
- Reports label the successful-repetition denominator as `success-only n=...` and show the
  number of non-success, non-skip repetitions omitted from it as `failed excluded=...`.
  The exclusion count includes recorded harness/infrastructure errors; skips did not run
  the agent and remain in execution coverage instead.

Failed repetitions are excluded because an early failure would otherwise make the tools in
successful runs appear variable. With fewer than two successful repetitions, variance is
not observable and the report shows `frequency=—` beside those counts. Frequency `1.0` is rendered as core use; lower
positive frequency is variable use. The fleet headline counts tasks with at least one
variable tool. Because the measurement is descriptive, external servers get the same metric
as local servers even when their tool names differ.

### Response-token cost

Every driver reports `result_chars` and `result_tokens` per Plane call. The character count
comes from the serialized result text actually observed by the harness. Token counts carry
an explicit provenance:

- The API driver may use a backend token counter. If none is available or it fails, it uses
  the shared deterministic character estimate.
- CLI drivers estimate from the proxy-recorded character count by default.
- With `--record-result-payloads`, CLI sidecars also retain the result text. The parent
  harness uses `tiktoken` with `cl100k_base` when importable and otherwise falls back to the
  same estimate.

The estimate is `ceil(result_chars / 4)` for non-empty results. Rows and calls record
whether their values are measured, estimated, or mixed, and the report marks estimated and
mixed columns. An estimate is never presented as a measured tokenizer count.

Payload recording is off by default because tool results contain live workspace data and
make sidecars larger. The character-derived estimate remains useful for surface comparison
because it is deterministic and monotonic in the recorded response size.

### Provenance: what counts as proof the answer came from the surface

A read verifier asks two independent questions — is the answer right, and did the agent get
it from the tool surface. This is the definition of the second one. It is a property, not a
list of approved call sequences: an enumeration of routes through 183 actions can never be
complete, and each gap in it fails an agent that answered correctly by an unlisted route.

**A sentinel proves itself.** A sentinel is a per-run random string a seeder wrote into
Plane — a state name, a work item title, a comment phrase. It exists nowhere else, and the
agent's only route to Plane is the surface under measurement. So if a sentinel appears in a
response the agent received, the agent used the surface. Nothing further is required: not
which entity the request named, not which tool was called, not how many calls it took.

**A count does not.** Where the seeded truth is a number, presence proves nothing — a small
integer appears by coincidence. Aggregate evidence therefore keeps a target binding: an exact
`total_count` counts only from a request whose arguments name a seeded entity. R2 binds one
project's count; L2 binds a work item's activity count; R6 accepts either one count per
project or one count grouped by project, since both are honest routes to its answer.

Matching happens while the successful response is in memory, and only a non-sensitive
`observed_sentinels` label is persisted. Read-task provenance does not turn payload recording
back on. CLI proxies receive sentinel lengths and SHA-256 fingerprints, plus the target IDs the
aggregate rule needs, through a mode-0600 run-scoped file outside the agent cwd; the raw value
is absent even from that file. Every MCP proxy session reads the same file, and the driver
removes it with its temporary directory after the run. The response body never enters the
result row, and the evidence machinery persists only the matched label. The sentinel itself
is not secret after the fact: a correct answer to R1 *is* the seeded state name, so it
appears in the recorded `final_text`, and a failing verifier note names the value it wanted.
Result rows are therefore run data, not a redacted artifact. Unavailable or incomplete
matching is diagnosed and cannot pass a read verifier.

### Threat model: a cooperative agent

The harness measures an agent that is trying to do the task, not one trying to defeat the
measurement. That is a deliberate scope decision, and the numbers should be read with it in
mind.

The agent under measurement runs as the same OS user, in the same filesystem, holding the same
Plane credentials as the harness. An agent that wanted to could read the evidence
configuration, edit an earlier row in the result file, fabricate a proxy sidecar session, or
skip MCP and call Plane directly — and off-surface indicators report such signals without
gating success. None of this is closed by any verifier rule; closing it requires running the
agent in a separate trust domain, which is a different project.

For the question this harness exists to answer — how well does our own tool surface serve a
capable agent — a cooperative agent is the right subject. Treat every integrity property above
as holding against accident and drift, not against an adversary.

Provider usage is a different measurement: where the driver supplies it, the harness keeps
input, output, cache-read, and cache-creation usage. Tool-result sizing describes one source
of context growth; it is not substituted for the provider's conversation-level usage.

## Why calls are recorded at the transport boundary

An agent's final answer is not a reliable call log. It may omit a failed lookup, summarize
several calls as one action, or claim an action it did not perform. Call-count and tool-use
metrics therefore come from execution evidence.

The API driver owns the MCP session and records each call it executes. The four CLI drivers
put `evals.proxy` between the CLI and the stdio MCP server. The proxy relays JSON-RPC bytes
without reserializing them, pairs `tools/call` requests and responses by JSON-RPC ID, and
records a request sequence on each sidecar row. The sidecar loader restores request order.
A complete proxy sidecar is authoritative; CLI event or transcript parsing is retained as
a fallback when the sidecar is incomplete. Neither source depends on the agent describing
its own behavior.

The proxy remains standard-library-only because it runs inside the server process tree with
a scrubbed `PYTHONPATH`. It records response payloads only when explicitly requested.
Tokenization and row mapping happen later in the parent harness, where optional dependencies
are safe to import.

## Driver and backend boundaries

All five driver implementations satisfy `AgentDriver.run_task(...) -> AgentRun`:

- `ApiDriver`
- `ClaudeCliDriver`
- `CodexCliDriver`
- `AntigravityCliDriver`
- `OpencodeCliDriver`

The runner has one path for all drivers: it supplies a prompt and MCP environment, receives a normalized
`AgentRun`, maps it to the common row shape, and invokes the task verifier.

The API implementation has one further seam. `ApiDriver` owns provider-independent policy:
the stdio MCP session, tool execution loop, iteration budget, timing, result recording,
call-ID pairing, and usage accumulation. A `ModelBackend` owns provider conversation state
and wire format through three operations:

```text
start(system, prompt, tools)
next_turn() -> Turn
add_tool_results(results)
```

This is the narrowest boundary that keeps provider-specific message roles, content blocks,
tool schemas, usage objects, and stop reasons out of the loop. `AnthropicBackend` translates
to the stable Messages API. `OpenAIBackend` translates to Chat Completions function tools
and imports the optional OpenAI SDK only when no client was injected. Both return neutral
turns containing text, tool calls, normalized usage, and a stop reason.

CLI agents already own their model conversation and tool loop, so they implement
`AgentDriver` directly rather than pretending to be `ModelBackend` implementations. Their
subprocess, configuration, transcript, and usage differences stay within their driver
modules.

CLI MCP configuration is isolated from ambient user state, but the strength of the
effective-config evidence differs by vendor:

| Driver | Effective-config exclusivity evidence |
|---|---|
| Claude | **Readback-supported, not behaviorally proven for the evaluated invocation.** Real `claude mcp list` reads the same isolated `.claude.json` and observes only `plane`. The evaluated `claude -p` receives that file plus `--strict-mcp-config`; exclusion of project/ambient MCP servers rests on the CLI's documented strict-config contract, not a forbidden-server probe of that invocation. HOME, `CLAUDE_CONFIG_DIR`, and all XDG roots are isolated. |
| Codex | Proven by real `codex mcp list --json` readback under the isolated Codex home. |
| OpenCode | Proven by real `opencode debug config` readback under isolated HOME/XDG roots and the generated project config. |
| Antigravity | **Unverifiable.** Antigravity CLI 1.1.13 has no MCP/effective-config introspection command. The harness isolates HOME/XDG roots and inspects generated files, but neither the harness nor this design treats that as observed effective-config exclusivity. |

The Antigravity "unverifiable" regression test is documentation coverage: it guards this
claim, not runtime behavior. Separate behavioral tests cover HOME/XDG isolation and generated
file placement, but those still cannot observe Antigravity's effective server set.

Several loop rules are deliberately centralized in `ApiDriver`:

- Tool results are paired to model calls by call ID, never by list position. Missing,
  duplicate, or unknown IDs set `result_pair_mismatch`.
- A refusal-terminated turn records any included calls for audit but executes none of them.
- `hit_max_iterations` is set only when the iteration budget is exhausted while more tool
  work remains, not merely because a valid final response used the last iteration.
- `wall_time_s` covers the model/tool loop after `list_tools`; server startup, teardown, and
  post-loop token counting are outside it.
- The row records the requested model token, requested tier (when present), resolved model ID,
  and the provider-reported model that actually ran when the provider returns one.

## Task and run lifecycle

The task catalog uses plain dictionaries. Each task stays beside its verifier in the module
for its task class. The catalog package assembles those lists in a pinned historical order,
builds `TASKS_BY_ID`, and computes the battery fingerprint.

For each task repetition, the runner creates a fresh project and only the fixture groups
declared by that task. The live sequence is:

```text
seed -> drive -> verify -> capture non-secret seed shape -> teardown -> append row
```

The row is assembled as the task progresses; teardown runs in `finally` before that row is
appended. Workspace-scoped fixture objects are tracked separately from the project. A fresh
stdio server is launched for each driven task. The server environment is built from `PATH`,
`HOME`, the three Plane connection values, and explicit `--server-env` additions; unrelated parent environment variables
are not inherited.

Result rows retain only seeded entity kinds and randomization namespaces. They never contain
target entity IDs or randomized truth values. Each repetition has an independent persisted
`fixture_seed_id`, and the randomized truth it derives is recoverable from that seed plus
namespace without exposing any later repetition's independent sentinel.

That seed is not a replay recipe for the whole fixture. Seeding also reads `date.today()` for
cycle and work-item dates, and identifier collisions retry with fresh `secrets` randomness
drawn outside the seeded namespace, so re-running a seed on another day — or after a
collision — does not reconstruct the same fixture. What the seed guarantees is narrower and is
what it was built for: each repetition's sentinels are independent, so no repetition can leak
another's answer.

The first line of a new result file is a meta row containing the run identity, label, server,
battery, requested model/tier, resolved model, driver, provider, Git SHA, exact task-id list,
and repetition count. Reports compare raw `(task_id, rep)` histories with that exact set
before latest-wins deduplication. Resume checks run identity and only appends replacements.
A repeated key is valid only when every occurrence except the authoritative last row is
retryable; prior terminal rows remain genuine duplicates and make the run incomplete.
Result rows preserve the common fields consumed by `evals.report` and existing JSONL readers.

## Module layout

```text
evals/
  cli.py                 argparse, command dispatch, and model-tier resolution
  __main__.py            command entry point for python -m evals
  runner/
    __init__.py          public execution API
    live.py              live lifecycle and row assembly
    resume.py            resume skip and mismatch checks
    meta.py              run metadata and repository provenance
    canary.py            empty-agent verifier canary
  tasks/
    __init__.py          public task API re-exports
    catalog.py           ordered catalog assembly and fingerprinting
    prompts.py           task prompt binding
    answers.py           answer-contract matching
    lookups.py           Plane reads used to establish verifier truth
    skip.py              task skip signal
    read.py              R1-R7 tasks and verifiers
    write.py             W1-W11 tasks and verifiers
    schema.py            S1-S5 tasks and verifiers
    cross.py             C1-C2 tasks and verifiers
    debias.py            I1-I5 and L1-L5 tasks and verifiers
  drivers/
    __init__.py          the driver registry, loading only the surface it is asked for
    api/
      base.py            neutral backend protocol and turn/tool dataclasses
      driver.py          the owned model/tool loop
      anthropic.py       Anthropic Messages translation
      openai.py          OpenAI Chat Completions translation
    cli/
      base.py            the subprocess template vendors fill in
      process.py         subprocess lifecycle
      sidecar.py         recording-proxy command and sidecar handling
      claude.py          Claude Code CLI driver
      codex.py           Codex CLI driver
      antigravity.py     Antigravity CLI driver
      opencode.py        OpenCode CLI driver
  core/                  shared floor: may import only core (+ stdlib/third-party)
    changelog.py         changelog text normalization helpers
    errors.py            neutral exceptions (TaskSkipped, …)
    evidence.py          target-binding evidence labels and sentinels
    fixtures.py          seeded fixture name/title constants
    results.py           run/task result types and common row mapping
    server_env.py        stdio MCP server env construction
    state_oracle.py      Plane state lookups used as verifier truth
    task_metadata.py     task tags/needs/prompt persisted in the run's meta header
    token_counting.py    tool-result token sizing
    tool_manifest.py     tools/list capture and fingerprinting
    tool_names.py        whose MCP tool a call is, and what to call it
  proxy.py               stdlib-only JSON-RPC recording relay
  seed/                  Plane fixture creation and teardown
  report/                summaries, A/B comparison, and multi-surface tables

```

Booting a Plane instance to measure against is deliberately outside this tree. The
harness reaches its target through three `EVAL_PLANE_*` variables and knows nothing
else about how that instance runs, so a local plane-ee, a shared staging box, and a
hosted workspace are the same thing to it.

The stable import and command surfaces are intentional: `from evals.tasks import ...`,
`from evals.drivers import ...`, and `python -m evals` remain the public boundaries even
though their implementations are split across packages and focused modules.
