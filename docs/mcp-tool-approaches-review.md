# Plane MCP — approaches to tool & token optimisation

**Status:** for review · **Date:** 2026-08-11 · **Baseline:** `plane-mcp-server` v0.2.11, 177 tools

**The problem in one line:** we ship 177 tools on every connection, which is ~130k tokens of schema
sent before the user types anything — and more tools than several MCP clients will even load.

This document sets out every approach we evaluated, with measured numbers where we have them and
sourced third-party numbers where we do not. It ends with a recommendation.

---

## 1. Executive summary

| | Today (v1) | Recommended (v2) |
|---|---|---|
| Tools advertised | 177 | **28** |
| Wire payload | 500,533 chars | **56,639 chars** |
| Tokens (o200k) | 130,303 | **13,566** |
| Share of a 200k context, at rest | **63%** | **7%** |
| Fits Cursor's 40-tool cap | No — 137 dropped | **Yes**, 12 slots spare |
| Capability reachable on Cursor | 14 of 45 needed tools | **all** |
| Backwards compatible | — | 169 of 177 old names still work |

**Four things worth a leader's attention:**

1. **This is a correctness problem before it is a cost problem.** Cursor loads the first 40 tools and
   silently drops the rest. Today a Cursor user cannot create a cycle, label, state, page or module —
   the features exist, the agent just cannot see them.
2. **Tokens and the tool cap are two different problems.** Schema hygiene alone gets −70% of the tokens
   while leaving 177 tools — so it fixes the bill and not the P0. Only a smaller surface fits the cap.
   The evidence is in §7a, the most decision-relevant table here.
3. **The saving is not a vendor artefact.** −90% on `o200k`, −90% on `cl100k`, −89% in raw bytes. Every
   ratio here holds whatever model the client runs.
4. **It unblocks the roadmap.** On the flat surface every new capability costs a permanent tool slot in
   a budget we are already 137 over. That is why the capability queue is frozen. Under the recommended
   surface a new capability costs ~96 tokens and no slot.

---

## 2. Approaches evaluated

Each is scored on the metrics that matter: tools advertised, tokens at rest, and the behavioural cost.

| # | Approach | Tools | Tokens at rest | Accuracy | Infra needed | Verdict |
|---|---|---|---|---|---|---|
| 1 | Current 1:1 API mapping | 177 | 130,303 | baseline | none | **Status quo — failing** |
| 2 | Microsoft Work IQ — generic verbs + paths | **10, fixed** | very low | unpublished | **policy layer + served schema** | **Phase 3 target** |
| 3a | Code Mode (Cloudflare) | 2 | ~1k | model-dependent | **sandbox required** | **Rejected — phase 3+** |
| 3b | Stateless MCP (2026 spec) | n/a | n/a | n/a | none | **Phase 2 — orthogonal** |
| 4 | Gating tools behind tool search | 5 | ~1,200 | **worse** | none | **Rejected — measured** |
| 5a | Open PR #195 (community) | 29 | ~59,783 wire | neutral | none | **Right idea, taken further** |
| 5b | Four-tier consolidation, 139→47 | 47 | 22,772 re-measured | unmeasured | none | **Tiering adopted, 47 not** |
| 6 | **Consolidated surface (recommended)** | **28** | **13,566** | **neutral** | **none** | **Ship** |
| 7 | Orthogonal levers (schema hygiene, caching, result bounding) | n/a | see §7 | n/a | none | **Partly shipped, partly phase 2** |

---

### 1. Current state — 1:1 API/SDK mapping

Every SDK endpoint gets its own MCP tool: `list_labels`, `create_label`, `update_label`,
`delete_label`. 177 tools, registered unconditionally on every transport.

**Pros**
- Maximally explicit: each tool has a narrow schema, so required vs optional is unambiguous.
- Zero indirection — a tool maps to exactly one API call.
- No abstraction for the model to reason through.

**Cons**
- **Exceeds every published client cap** (Cursor 40, Windsurf 100, VS Code/Copilot 128).
- 130,303 tokens re-sent on *every* model request, not once per session.
- Crowds out the user's other MCP servers — we overrun a 40-slot budget by 137.
- 177 overlapping names invite near-miss selection (measured: `search_work_items` chosen where
  `list_work_items` was needed, in 9 of 50 benchmark tasks).

**Metrics:** 177 tools · 500,533 chars · 130,303 tok · 63% of a 200k window · 31 of 45 needed tools
unreachable on Cursor.

---

### 2. Microsoft Work IQ MCP — generic verbs over resource paths

The most architecturally ambitious approach in the market, and the one worth understanding properly.
Microsoft's Work IQ MCP exposes **all of Microsoft 365 through 10 fixed tools**. The tool is the verb;
the resource path is the argument:

```
fetch          /me/messages                  → read my emails
do_action      /me/sendMail                  → send an email
create_entity  /me/events                    → create a calendar event
call_function  /search/query                 → semantic search
ask            "What deals closed this quarter?"  → invoke Copilot
```

Ten tools in three groups: **entity** (`fetch`, `create_entity`, `update_entity`, `delete_entity`,
`do_action`, `call_function`), **Copilot** (`ask`, `list_agents`), **schema** (`get_schema`,
`search_paths`).

Three stated design principles, each a deliberate trade:

| Principle | What it means | What it costs |
|---|---|---|
| **Fewer tools, more paths** | New workloads add *paths*, not tools. Surface fixed at 10 forever. | The model must know or discover the path — it is no longer in the tool list |
| **Introspection over enumeration** | Agents call `get_schema` at runtime instead of loading thousands of type definitions | A round-trip before real work; wrong guess = retry |
| **Policy over scopes** | 4 broad OAuth permissions; real authorisation enforced per path/method/tenant (Rego policy) | Authorisation moves out of the tool surface into a policy layer you must build and operate |

**Pros**
- **The only option whose tool count never grows.** Ours grows with each new resource; theirs is
  capped at 10 by construction. This is the highest ceiling of any approach here.
- Very low tokens at rest — 10 small schemas.
- Genuinely elegant: one uniform grammar instead of N hand-written tools, so no per-tool drift.
- `ask` offloads multi-step reasoning to a server-side intelligence layer, cutting round-trips that a
  pure tool surface would need.

**Cons**
- **Authorisation leaves the tool surface.** `do_action /<anything>` is a single tool that can do
  anything. Microsoft answers this with a Rego policy engine and per-path/tenant enforcement — that is
  a component we would have to build, operate and secure. It is the real cost of this design, and it
  is not small.
- **Per-operation safety metadata collapses.** Measured on our surface: 183 actions carry
  `readOnlyHint` / `destructiveHint` today (74 read, 30 destructive). Folded onto ~10 generic verbs, a
  client can no longer tell a read from a delete. Concretely this breaks Claude Code's Plan mode
  (our issue #198) and every client's destructive-action confirmation prompt.
- **Same introspection cost we already measured and rejected.** `search_paths` + `get_schema` is
  progressive disclosure — structurally what we benchmarked in §4, where it cost **+85% total
  context**, 2.3× the calls and 3× the errors. Work IQ mitigates this with `ask`; we have no
  equivalent server-side reasoning layer.
- Path hallucination becomes a new failure mode: a plausible-but-wrong path fails at the API, not at
  schema validation.
- Works best when the API is enormous. Microsoft Graph is orders of magnitude larger than Plane's API;
  the design pays for its complexity at that scale.

**Feasibility for Plane — we checked, and it is better than expected**

| Precondition | Plane today | Verdict |
|---|---|---|
| Uniform, hierarchical path grammar | Yes — `workspaces/<slug>/collections/<id>/members/<id>/` throughout | **Present** |
| Served OpenAPI schema for `get_schema` | `drf_spectacular` wired behind `ENABLE_DRF_SPECTACULAR`, with a committed OpenAPI document and a drift test; api_v2 has schema-render contract tests | **Largely present** |
| Policy layer for generic-verb authorisation | None | **Missing — significant build** |
| Server-side reasoning layer (their `ask`) | None | **Missing** |

**Metrics:** 10 tools, fixed regardless of API growth. Token cost at rest not published; ~10 small
schemas implies low four figures. No independent accuracy or latency numbers published.

> **Assessment: the right long-term target, wrong phase.** Two of the four preconditions already exist
> in our API — that is a genuinely useful finding and it makes this credible for **phase 3 alongside
> api_v2**, not a distant idea. What blocks it now is the policy layer and the annotation regression:
> we would be trading a working safety-metadata story for one we have to rebuild. Adopting it today
> would also mean paying the introspection cost we have already measured as a net loss.
>
> **What we take from it now:** the "paths, not tools" instinct is why our recommended surface uses a
> uniform `action` grammar and a scope idiom (`project_id` present or absent) rather than minting a new
> tool per variant — so adding a capability costs an action, not a tool. That is the same principle
> applied at a scale our infrastructure supports today.

*Related, and often confused with the above:* Microsoft's **`mcp-cli`** implements dynamic tool
discovery host-side — the host pulls tool definitions only when needed. Near-zero tokens at rest, but
it is a **host/CLI pattern, not a server one**: we do not control Cursor, Claude Code or Antigravity,
so we cannot ship it to our users. A leaner server surface helps users on every client, including
those with no discovery mechanism at all.

---

### 3. Other current approaches

**3a. Code Mode (Cloudflare).** Replace tool definitions with `search()` + `execute()` and a typed
SDK; the agent writes code in a sandboxed isolate and only results return to context.

- *Pros:* handles enormous surfaces (reported 1.17M → ~1,000 tokens, −99.9% for 2,500+ endpoints);
  chains operations without round-trips.
- *Cons:* **requires sandbox infrastructure we have ruled out for this phase**; depends on the model's
  code quality; the payoff is aimed at APIs an order of magnitude larger than ours.

**3b. Stateless MCP (July 2026 spec).** Server holds no per-session state; each request is
self-contained.

- *Pros:* horizontal scaling, simpler ops, better fit for our hosted deployment.
- *Cons:* **orthogonal to tool count** — it does not reduce a single token of schema.

> **Assessment:** 3a is rejected for phase 1 on the no-new-infrastructure constraint. 3b is worth
> doing but belongs in phase 2, because it solves a different problem.

---

### 4. Gating tools behind tool search

Advertise ~5 tools (`search_tools`, `call_tool`, …); the model searches for what it needs and the
schema is loaded on demand. We prototyped this with FastMCP's search transforms.

**Pros**
- Smallest possible listing: ~1,200 tokens at rest.
- Server keeps its full capability surface unchanged.
- Vendor-reported results are strong: Anthropic's Tool Search (GA Feb 2026) reports −85% tokens with
  accuracy *improving* (Opus 4 49% → 74%; Opus 4.5 79.5% → 88.1%).

**Cons — and this is the important part, because we measured it ourselves**
- **Total context went up, not down.** Saving 65k on the listing cost 174k in extra results and
  retries.
- **2.3× more tool calls** (314 vs 137) and **3× the errors** (12 vs 4).
- **+23% median latency** (12.5s vs 10.2s).
- Search quality becomes a new failure mode: if the model's phrasing does not match our descriptions,
  the capability is invisible and it retries.

**Metrics (our 50-task benchmark, live workspace):**

| Surface | Tools | Listing tok | Result tok | **Total ctx** | Calls | Errors | Median latency |
|---|---|---|---|---|---|---|---|
| Flat (177) | 177 | 66,129 | 61,960 | 128,089 | 137 | 4 | 10.2s |
| Consolidated (29) | 29 | 23,687 | 69,435 | **93,122** | 142 | 8 | 10.3s |
| Flat behind search | 5 | ~1,200 | 235,520 | **236,720** | 314 | 12 | 12.5s |

> **Assessment: rejected on our own evidence.** Optimising the listing in isolation is the wrong
> objective — the listing is only a third of the context. Note the honest caveat: Anthropic's Tool
> Search is a *client-side* feature with better integration than our server-side prototype, so its
> published numbers are not directly comparable. If Tool Search becomes ubiquitous client-side, a small
> well-described surface benefits from it too — the two are complementary, not competing.

---

### 5. Prior proposals, measured

Two consolidation proposals predate this work. Both are credited and both were measured rather than
argued about; the recommended design in §6 borrows from each.

#### 5a. Open PR #195 (community contribution)

One action-dispatch tool per resource: `list_labels` / `create_label` / `update_label` → `label` with
an `action` parameter. 177 → 29 tools, opt-in behind a `--v2` CLI flag.

**Pros**
- **Correct core insight**, independently confirmed by our measurements: consolidation is where the
  win is, and it needs no new infrastructure.
- Identified the real cost driver — `outputSchema` was 67% of the payload.
- Ships both surfaces side by side, so migration is safe.

**Cons**
- Left the 177 old tools untouched and unmapped — an upgrade silently breaks every saved prompt and
  script using the old names.
- Abstracted parameters weaken the required/optional contract, which is where LLM argument errors come
  from.
- Left the `outputSchema` decision open as a question rather than settling it.
- CLI-flag selection does not reach users of the hosted server.

**Metrics (as reported in the PR):** 177 → 29 tools; wire payload 125,649 → 59,783 (−52%), or 42,243
with compression (−66%), or 15,837 with untyped params (−87%).

> **Assessment: right idea, and we have built on it rather than around it.** The gap between −52% and
> our −89% is the `outputSchema` question the PR left open, which we settled by measurement (see §6).

#### 5b. The four-tier consolidation proposal — 139 tools → 47

A per-tool review of the whole surface with a verdict on each (KEEP / MERGE / FOLD / CUT), grouped into
four tiers: **Core** loaded always, **Extended** on demand, **Schema** and **Admin** behind an explicit
step. Every verdict is justified against how ClickUp, Linear, Jira, Asana and Monday handle the same
capability.

**Pros**
- **The tiering insight is the strongest single idea in any proposal here**, and it survives
  measurement. On our 28-tool surface a Core-equivalent set of 8 tools is 4,495 tokens — **33% of the
  listing and −97% against today**. Tiering, not consolidation, is what reaches single-digit thousands.
- Correctly identifies that our most differentiated capabilities (custom work-item types, custom
  relation types, estimates — things no competitor exposes) are also the least used per session. Our
  Schema-equivalent tools are **21% of the listing** for capabilities most sessions never touch. Its
  conclusion — the moat is that those tools *exist*, not that they are resident — is right.
- **FOLD is the best result-side idea we have.** Making comments, links, relations, attachments and
  activity fields of a work item via `include=` attacks result tokens, which are ~3× the listing
  (§7d). No other proposal touches that.
- Useful competitive grounding, and it surfaced real capability *gaps* rather than only excess: no page
  editing, no bulk update, no workflow-transition visibility.

**Cons**
- **47 is the wrong target, and tool count is the wrong unit.** At our measured consolidated density
  (484 tok/tool), 47 tools is **22,772 tokens — 68% more than 28**. A consolidated tool's description
  enumerates every action and its schema unions every parameter, so spreading the same capability
  across more tools costs more, not less.
- Its context estimate extrapolated a third-party figure for Linear (~750 tok/tool). Measured, our
  density is 736/tool flat but **484/tool consolidated** — so the projection was about right for the old
  surface and ~50% high for the new one. Density must be measured per surface, not carried across.
- **The CUT verdicts break pinned clients for no token benefit.** Once a name is merely unadvertised
  rather than deleted it costs zero tokens and every saved prompt keeps working. Removing it buys
  nothing and breaks someone.
- **Tiering by environment variable serves self-hosted only.** One hosted process serves every tenant,
  so the only globally correct value is "everything". Deriving the surface from the workspace's enabled
  features is strictly better there — automatic, more accurate, no user decision.
- Putting Schema and Admin behind "an explicit schema-work step" is progressive disclosure — the
  mechanism we measured at **+85% total context** (§4). The tier boundary is right; making the model
  discover across it is the part to avoid.
- Four named tiers is a taxonomy users must learn. One knob meaning "less than everything" gets the
  benefit without the vocabulary.

**Metrics:** 139 → 47 tools; Core tier of 18 ≈ 13k tokens as proposed. Re-measured at our density: 47
tools = 22,772 tok; an 8-tool Core = 4,495 tok (−97% vs today).

> **Assessment: adopt the thinking, not the number.** We took the tier structure, the FOLD/`include=`
> idea, archive-by-default, human-readable identifiers as primary, and the capability-gap list. Three
> things changed: the target is 28 rather than 47 (fewer tools is cheaper, measured); CUT becomes
> unadvertised-but-callable, so nothing breaks; and tiering is **deferred out of phase 1 entirely**,
> because at 28 tools we already fit every published client cap — the knob would be configuration
> nobody needs yet. Tiering becomes an optimisation rather than a fix, and should ship when a
> self-hosted operator asks for it or when least-privilege scoping becomes a requirement.

---

### 6. Recommended — the consolidated surface we have built

One tool per resource with a typed `action` parameter, `outputSchema` stripped from the listing, and
every old tool name still callable. **28 tools, 13,566 tokens, no new infrastructure.**

**What we settled that was previously open:**

| Question | Answer | How we know |
|---|---|---|
| Does `outputSchema` reach the model? | **No** — it is a client-side validation contract | Spec read + provider API test; sending it returns 400 |
| Is `chars ÷ 4` a safe estimate? | **No** — understates Claude by ~69% | Measured: 2.37 chars/tok Claude vs 4.14 GPT-4o |
| Does consolidation improve accuracy? | **No — it is neutral** | The +7pp claim was a scoring artifact; re-scored fairly it is −2pp |
| Does tool search help? | **No — it costs 85% more context** | Our own 50-task benchmark |

**Pros**
- Largest measured reduction of any no-new-infrastructure option: **−84% tools, −90% tokens**.
- Fits every published client cap with room for the user's other servers.
- **169 of 177 old tool names still resolve**, so existing integrations keep working. The 8 exceptions
  are tools that chose between two operations via a parameter; each returns an error naming its
  replacement.
- Governance-ready: workspace-vs-project scope is declared once, so extending it to states and labels
  is a declaration, not a rewrite.
- Enforced by 628 automated tests, including a validating SDK stand-in that type-checks every call.

**Cons — stated plainly**
- **Parameter dilution.** An action sees on average 47% of its tool's parameters as relevant; for
  `work_item create` it is 8 of 35. Mitigated by generating a per-action parameter list into the
  description, but not eliminated. This is inherent to action-dispatch at *any* grouping — the
  alternative (more tools) trades it for a worse failure, wrong-tool selection.
- **Accuracy is neutral, not better.** We are not claiming a quality win, and the case does not
  depend on one.
- v1 must be maintained for one release as a fallback.

**Metrics:** 28 tools · 56,639 chars · 13,566 tok (o200k) / 13,121 (cl100k) · 7% of a 200k window ·
169/177 names compatible · 628 tests passing.

---

### 7. Orthogonal levers — measured separately, because they answer different questions

Everything above changes the *shape* of the surface. These change its *cost* without changing its
shape, and they apply to any of the options above. The first one matters most, because it separates
two problems that are easy to conflate.

**7a. Schema hygiene — drop `outputSchema`, compress the rest.** `outputSchema` is a client-side
validation contract; no provider forwards it to the model. Separately, FastMCP inlines every nested
Pydantic model rather than `$ref`-ing it, and Pydantic renders each optional field as a verbose
`anyOf`-with-null block. Both are mechanically fixable and semantically lossless.

Measured on our own listing (o200k tokenizer):

| Variant | Tools | Tokens | Cut | Fits Cursor's 40 cap |
|---|---|---|---|---|
| v1, as shipped today | 177 | 130,303 | — | **No** |
| v1 + schema compression only | 177 | 112,399 | −14% | **No** |
| v1 + drop `outputSchema` | 177 | 39,552 | **−70%** | **No** |
| v1 + both | 177 | 36,888 | −72% | **No** |
| **v2, 28 tools (recommended)** | **28** | **13,566** | **−90%** | **Yes** |
| v2 + schema compression | 28 | 13,344 | −90% | Yes |

> **This is the most important table in the document.** It shows the token problem and the capability
> problem are *different problems with different fixes*:
>
> - **Tokens** are fixed mostly by hygiene. Dropping `outputSchema` alone is −70%, cheap and low-risk.
> - **The client cap — the P0 — is fixed only by having fewer tools.** No amount of compression makes
>   177 tools fit in 40 slots. A Cursor user still cannot create a cycle or a label.
>
> Two further conclusions. Compression *without* dropping `outputSchema` is nearly pointless (−14%):
> the bulk was always the output schemas. And compression *on top of* v2 buys 222 tokens (−1.6%),
> because v2 already carries no output schemas and few nested models — so **we are not shipping the
> compression pass**, and that is one less moving part to maintain.

**7b. Prompt caching (`cache_control`).** Tool definitions sit at the front of the cache prefix, so a
stable listing can be cached across turns.

- *Pros:* materially cuts per-turn cost and latency; free where the client enables it; our deterministic
  tool ordering already makes the prefix cacheable.
- *Cons:* reduces *cost*, not *context occupancy* — the tokens still take up window. Not enabled by
  every client, and any change to the listing invalidates it.
- *Verdict:* worth keeping compatible with (we do: ordering is deterministic and tested), not a
  substitute for a smaller surface.

**7c. Expose reads as MCP Resources rather than Tools.** Resources do not count against tool caps.

- *Pros:* would move read-only surface out of the tool budget entirely.
- *Cons:* client support is uneven, and resources are not model-invoked the same way — a model that
  cannot call it will not use it. We already hit this: in PR #158 we deliberately kept `search_docs` a
  tool rather than a resource for exactly this reason.
- *Verdict:* no, on client-support grounds. Revisit if resource support becomes universal.

**7d. Bound the result payloads.** The next real frontier, and the one this document does *not* solve.
Measured in the 50-task benchmark, **tool results are roughly three times the listing** (61,960 vs
23,687 tokens on the consolidated surface). Roughly one call in four exists only to turn a name into a
UUID.

- *Pros:* attacks the largest remaining share of context; independent of tool count.
- *Cons:* trimming fields risks removing something the model needed; needs care and its own benchmark.
- *Verdict:* **phase 2**, and it is where the next big win is. Worth saying out loud: fixing the
  listing is necessary but not sufficient.

**7e. Operator-selected tool tiers.** An env var allowlist so a deployment exposes only, say, a core
tier of 8 tools.

- *Pros:* smallest possible surface per deployment; a Cursor user could fit Plane alongside 4 other
  servers.
- *Cons:* every knob is a support burden and a way to silently lose capability — a user reporting "Plane
  can't create labels" now has a configuration cause as well as a code cause. It also does nothing for
  the hosted default, which is where most users are.
- *Verdict:* **cut.** We removed tiering from phase 1 deliberately. 28 tools already fits every
  published cap, which was the reason tiering existed.

---

## 3. Recommendation

**Ship the consolidated surface (§6) as phase 1**, default-on, with v1 available via
`PLANE_MCP_TOOLS_VERSION=v1` for one major release.

| Phase | Scope | Why this order |
|---|---|---|
| **1 — now** | 28-tool consolidated surface, `outputSchema` off the wire, legacy-name compatibility, conformance tests | Removes the capability loss today, needs no new infrastructure, reversible by one env var |
| **2 — next** | Bounded result payloads and sub-resource folding (§7d, §5b — results are ~3× the listing), stateless transport, tiering *if asked for* | The next real win is results, not the listing; tiering is an optimisation once the cap is no longer breached |
| **3 — later** | api_v2 surface, and evaluate a Work IQ-style generic-verb layer on it (§2): needs a policy engine and per-path authorisation, but the path grammar and OpenAPI document largely exist | Depends on api_v2 landing; the only approach whose tool count never grows |

**Deliberately not doing:** a second server URL (pushes migration onto every user in every client),
tiered tool config knobs (§7e), and the schema-compression pass (§7a — buys 222 tokens on v2, not worth
the moving part). Each adds configuration for a benefit we measured as marginal or absent.

---

## 4. Evidence and effort

Everything above is measured against the live server, not estimated:

- Token counts on **three tokenizers** (`claude-opus-5`, `o200k_base`, `cl100k_base`) plus raw bytes,
  to prove the result is not a vendor artefact.
- A **50-task benchmark** against a live Plane workspace, recording every call, argument, error and
  latency across three candidate surfaces.
- **Client cap reachability** cross-referenced against our actual registration order, to determine
  exactly which features a Cursor user cannot reach today.
- A **re-audit of our own benchmark**, which found the headline accuracy claim was a scoring artifact —
  we withdrew it rather than present it.
- **628 automated tests**, including a validating SDK stand-in that binds every call against the real
  `plane-sdk` signature. It caught real defects during the build: nested-payload and query-param type
  mismatches, and silent data loss where `0` (a valid "secret" project visibility and a valid sort
  position) was being treated as "not supplied".
- A **description audit**, which found the PQL reference — injected into the model on every query
  failure — was pointing at tool names that no longer exist, plus one that never existed. Now
  generated per surface and enforced by tests.

---

> **Coming next:** exact metrics tested against a standard sample-prompt suite — tool-selection
> accuracy, argument accuracy, retry/error rate, latency and end-to-end task success, per surface and
> per client — will be added to this document once the benchmark run completes.

**Sources for third-party figures:**
[Microsoft Work IQ MCP overview](https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/work-iq-mcp-overview) ·
[Anthropic Tool Search & Code Mode comparison](https://mcp.directory/blog/mcp-context-bloat-fix-2026-tool-search-code-mode-progressive-disclosure) ·
[Microsoft mcp-cli dynamic tool discovery](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/mcp-vs-mcp-cli-dynamic-tool-discovery-for-token-efficient-ai-agents/4494272) ·
[MCP context bloat at enterprise scale](https://agentmarketcap.ai/blog/2026/04/08/mcp-context-bloat-enterprise-scale-tool-definitions-agent-context-budget) ·
[Context bloat — modelcontextprotocol/python-sdk #2619](https://github.com/modelcontextprotocol/python-sdk/issues/2619)
