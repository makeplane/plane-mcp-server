# Plane MCP Tool-Surface Eval Harness

This harness measures how well an LLM agent completes real Plane tasks through an MCP
tool surface. It exists to replace predictions about a surface with observations from
actual agent runs: whether the task succeeded, how many Plane calls it took, which tools
were selected, and how much tool-result content was returned to the model.

This document explains why the harness is shaped this way. Operational commands live in
`evals/README.md`.

## The questions it answers

The original tool-consolidation question breaks down into three measurable questions:

1. **Mispick rate** — how often does an agent choose an alternate or out-of-set tool when
   several tools have overlapping names or capabilities?
2. **Calls-to-done versus optimal** — how much lookup, name-to-ID resolution, and
   sub-object fan-out does the surface require before the task is complete?
3. **Response bloat** — how much tool-result content is injected into the conversation?

Success is the guardrail around all three. A surface that uses fewer calls or returns less
text but fails the task is not an improvement. Conversely, success rate alone hides
avoidable calls, wrong turns, and large responses. The harness therefore records all four
dimensions for the same task execution.

The point is empirical comparison. Given the same task battery, model, and repetitions,
different surfaces can be compared from observed behavior rather than from tool counts,
schema inspection, or projected costs. The battery fingerprint records the prompt and
tool-set definition used for a run so incompatible batteries are not silently compared.

## What is measured

### Success

Each task has an asynchronous verifier. Mutation tasks read Plane back through the API and
check the resulting state. Read tasks compare the final assistant text with facts obtained
from the seeded context or resolved through the API, using explicit answer contracts and
exact-value matchers where the task defines them.

This avoids using the agent's explanation, confidence, or self-reported completion as the
source of truth. The model is also not asked to grade another model. Verification is tied to
the fixture and the Plane state the task was meant to affect. The canary runs every eligible
verifier against an empty agent result and fails if a do-nothing run passes.

Skipped tasks and infrastructure failures are recorded separately. The report excludes
both from success denominators; a plan gate, unavailable fixture, provider failure, or MCP
process failure is not rewritten as an agent task failure.

### Calls to done

`num_calls` counts Plane MCP calls made during the task. Each catalog entry also declares an
`optimal_calls` baseline. The report shows the observed distribution rather than assuming
one run is representative.

Client-local tools such as shell or tool-search helpers are retained separately as
`client_tool_calls`; they do not count as Plane calls. For an external server launched with
`--server-cmd`, call counts still apply, but the runner marks the row server as `external`
and clears the row-level alternate/out-of-set counters because the catalog has no
authoritative sets for foreign tool names.

### Mispicks

Every Plane call on a catalogued surface is classified by tool name as `optimal`,
`alternate`, or `out_of_set`. The task owns disjoint optimal and alternate sets. The
headline mispick rate is:

```text
(alternate calls + out-of-set calls) / all Plane calls
```

`is_error` is independent of that classification. A valid call can still be an avoidable
pick, and an optimal tool can return an error. The ordered call records are retained in the
JSONL so a run can be audited after aggregation.

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

Provider usage is a different measurement: where the driver supplies it, the harness keeps
input, output, cache-read, and cache-creation usage. Tool-result sizing describes one source
of context growth; it is not substituted for the provider's conversation-level usage.

## Why calls are recorded at the transport boundary

An agent's final answer is not a reliable call log. It may omit a failed lookup, summarize
several calls as one action, or claim an action it did not perform. Call-count and mispick
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
seed -> drive -> verify -> teardown -> append row
```

The row is assembled as the task progresses; teardown runs in `finally` before that row is
appended. Workspace-scoped fixture objects are tracked separately from the project. A fresh
stdio server is launched for each driven task. The server environment is built from `PATH`,
`HOME`, the three Plane connection values, and explicit `--server-env` additions; unrelated parent environment variables
are not inherited.

The first line of a new result file is a meta row containing the run identity, label, server,
battery, requested model/tier, resolved model, driver, provider, and Git SHA. Resume checks those identities,
skips completed task/repetition keys, and reruns rows that contain recorded errors. Result
rows preserve the common fields consumed by `evals.report` and existing JSONL readers.

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
    write.py             W1-W10 tasks and verifiers
    schema.py            S1-S5 tasks and verifiers
    cross.py             C1-C2 tasks and verifiers
    debias.py            I1-I5 and L1-L5 tasks and verifiers
  drivers/
    __init__.py          public exports and driver registry
    driver.py            AgentDriver seam, the API loop, and the CLI template
    api/
      backend.py         neutral backend protocol and turn/tool dataclasses
      anthropic.py       Anthropic Messages translation
      openai.py          OpenAI Chat Completions translation
    cli/
      process.py         subprocess lifecycle
      sidecar.py         recording-proxy command and sidecar handling
      claude.py          Claude Code CLI driver
      codex.py           Codex CLI driver
      antigravity.py     Antigravity CLI driver
      opencode.py        OpenCode CLI driver
  results.py             run/task result types and common row mapping
  tool_names.py          whose MCP tool a call is, and what to call it
  token_counting.py      tool-result token sizing
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
