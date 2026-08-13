# Regression prompts for this round of fixes

Every prompt here **fails on the previous build and passes on this one**. That is the
point: each has a *before* value written next to it, so a pass is something you can see
rather than infer.

This is not a coverage battery — use `TEST_A` / `TEST_B` for that. This is the short set
that proves the specific things that changed.

## Before you start

Run on a **workspace-governed** workspace — you need `work_item_types: true`. Prompts
14–20 only mean anything there.

> Which features are enabled on this workspace?

Create a scratch project and keep the id; the last prompt deletes it.

> Create a project called Regress with the identifier RGRS.

You also need one work item with a known-true answer for prompt 1. Seed it deliberately:

> In Regress, create four urgent work items called Urgent A, Urgent B, Urgent C and
> Urgent D, and two low-priority ones called Quiet A and Quiet B.

**Truth for the rest of this doc: 4 urgent items in Regress.**

## What to record

| Column | Meaning |
| --- | --- |
| **result** | the actual answer or error text |
| **calls** | how many tool calls it took |
| **verdict** | pass / fail against the stated expectation |

Markers: **should work** (a failure is a bug) · **must fail** (succeeding is the bug —
judge the message too) · **record only** (behaviour not settled).

---

## A. The wrong-answer defect

### 1. should work — the R2 case

> How many urgent work items are in Regress?

**Expect** — **4**. Not a workspace-wide total.

**Before** — answered the whole workspace (the eval saw 332 where truth was 4), because
`count` ignored `project_id` and called `count_workspace`.

**Pass test** — the number equals the number of urgent items you seeded, not the
workspace total. Ask "and how many across the whole workspace?" to see them differ.

**Tools** `workitem:count` · **calls ≤ 2**

### 2. should work — grouped, still scoped

> Break down Regress work items by priority.

**Expect** — buckets covering only Regress. Sum equals 6, not the workspace count.

**Before** — five buckets summing to the whole workspace.

**Tools** `workitem:count` · **calls ≤ 2**

### 3. must fail — an argument the action does not take

Give this as a literal instruction, not a paraphrase:

> Call the workitem tool with action=count, project_id=<Regress id>, and query=urgent.

**Expect** — refused, naming the offending parameter *and* what `count` does take:
`Error: action 'count' does not take: query. It takes: group_by, pql, project_id, sub_group_by.`

**Before** — accepted, `query` silently dropped, and a confidently wrong number returned
in exactly the shape of a right one. This was the root cause of R2.

**Pass test** — the call never reaches Plane. A number coming back is a fail.

**Tools** `workitem:count` · **calls ≤ 1**

### 4. should work — padding is not an error

> Call workitem count for Regress but also pass empty strings for query, name and expand.

**Expect** — works normally. Arguments left at their defaults say nothing, so they are
ignored rather than refused. Some clients pad every parameter; they must not be punished.

**Tools** `workitem:count` · **calls ≤ 1**

---

## B. Calls that used to be wasted

### 5. should work — archiving confirms itself

> Archive Quiet A, then tell me whether it worked.

**Expect** — `{"workitem_id": "…", "archived": true}` from the archive call itself. The
agent should **not** follow up with a retrieve.

**Before** — returned nothing, so the agent verified with a retrieve, which 404s because
an archived item leaves the regular retrieve path. One guaranteed-failing call per item.

**Pass test** — zero errors, and no `retrieve` in the trace.

**Tools** `workitem:archive` · **calls ≤ 2**

### 6. should work — and unarchiving says so too

> Put Quiet A back.

**Expect** — `{"workitem_id": "…", "archived": false}`.

**Tools** `workitem:archive` · **calls ≤ 2**

### 7. should work — no pre-flight probe before a work log

> Log 45 minutes against Urgent A for debugging.

**Expect** — on a project with time tracking on, this is **one call**. The agent must not
read project features first.

**Before** — the tool description said "Time tracking is a per-project feature; the API
returns an error when it is not enabled", and the agent spent a features call every run
(measured 6 calls vs 3).

**Pass test** — no `project:get_features` in the trace.

**Tools** `work_log:create` · **calls ≤ 2**

### 8. record only — the same on a project without time tracking

> Log 20 minutes against a work item in a project where time tracking is off.

**Expect** — a 404 `Worklog is not enabled for the project`, then the agent enables it and
retries. **Known rough edge:** the error does not name the setting to change
(`project update` with `is_time_tracking_enabled`). Record the call count.

**Tools** `work_log:create` `project:update` · **calls: record**

---

## C. Feature flags that were unreachable

### 9. should work — flags that had no parameter

> Turn on project updates and parallel cycles for Regress.

**Expect** — accepted (or refused for a plan/governance reason, which is fine — the point
is the parameters exist).

**Before** — `project update_features` exposed no `epics`, `parallel_cycles`,
`project_updates` or `workflows`, so these were unsettable through the server at all.

**Pass test** — the agent does not report the capability as missing.

**Tools** `project:update_features` · **calls ≤ 2**

### 10. should work — time tracking is on the right action

> Enable time tracking for Regress.

**Expect** — goes through `project update`, not `update_features`.

**Before** — `is_time_tracking_enabled` was declared on `update_features`, where it was
never sent to the API. Passing it did nothing and reported success.

**Tools** `project:update` · **calls ≤ 2**

### 11. must fail — a plan gate names the feature that fired

> Turn on epics for Regress.

**Expect** — if the plan does not include epics, the message names **Epics** specifically,
not a generic label.

**Before** — a single hardcoded label meant one feature's name was reported for whichever
of five gates actually fired.

**Note** — on a governed workspace this is refused for governance instead, with
`Cannot enable project-level epics when workspace-level work item types are enabled`.
Either refusal passes; a bare 402 or a wrong feature name fails.

**Tools** `project:update_features` · **calls ≤ 2**

---

## D. The listing that broke the client

### 12. should work — bounded project list

> List the projects in this workspace.

**Expect** — a bounded page with `next_cursor` set. On a workspace with hundreds of
projects the response should be tens of KB, not hundreds.

**Before** — returned every project unbounded; at 265 projects that was 204,095 characters
and a hard token-limit failure, which also made "does project X exist?" unanswerable.

**Pass test** — the call returns at all, and `next_cursor` is present.

**Tools** `project:list` · **calls ≤ 1**

### 13. should work — paging still works, and you can still ask for more

> Show me the next page. Then show me just 5 projects.

**Expect** — the cursor pages on; `per_page=5` returns 5. The default must not override an
explicit page size.

**Tools** `project:list` · **calls ≤ 3**

---

## E. Governed workspace: properties end to end

These are the prompts that dead-ended before. **Expect zero errors across 14–18.**

### 14. should work — one call to get a usable type

> Create a work item type called Bug and make it usable in Regress.

**Expect** — one `resolve` call. On a governed workspace it creates at workspace scope and
imports into the project by itself, and never attempts the project-level write that
governance would refuse.

**Before** — six calls including a guaranteed 400.

**Pass test** — no `project:update_features` in the trace.

**Tools** `workitem_type:resolve` · **calls ≤ 2**

### 15. should work — a property, named the natural way

> Add a text property called 'Root cause' to the Bug type in Regress.

**Expect** — created, with `is_active: true`.

**Before** — `400 This resource is managed at the workspace level`, because the call named
the project. The retry at workspace scope then produced `is_active: false` and
`issue_type: null` — a property that could never hold a value.

**Pass test** — one call, no error, `is_active` true.

**Tools** `workitem_property:create` · **calls ≤ 2**

### 16. should work — the property is attached, not just created

> What properties does the Bug type have?

**Expect** — Root cause listed.

**Tools** `workitem_property:list` · **calls ≤ 2**

### 17. should work — the call that was impossible

> Create a Bug in Regress called 'Search returns stale results', then set its Root cause
> to 'stale cache key'.

**Expect** — the value is stored.

**Before** — `400 Property 'Root cause' is not applicable to this work item's type`,
permanently. The only attach route required `project_id`, and supplying `project_id` was
exactly what governance refused. No valid path existed.

**Pass test** — the value round-trips. Read it back to be sure.

**Tools** `workitem:create` `workitem_property:set_value` · **calls ≤ 3**

### 18. should work — read it back

> What is the Root cause on 'Search returns stale results'?

**Expect** — `stale cache key`.

**Tools** `workitem_property:get_value` · **calls ≤ 2**

### 19. should work — attach an existing property without a project

> Create a workspace-level text property called 'Owner team', then attach it to the Bug
> type.

**Expect** — both succeed with no `project_id` anywhere.

**Before** — `manage_type_properties` required `project_id`, so this was impossible on a
governed workspace: with the project id it was refused, without it the parameter was
missing.

**Tools** `workitem_property:create` `workitem_property:manage_type_properties` · **calls ≤ 3**

### 20. should work — and detach it again

> Take 'Owner team' off the Bug type, but don't delete the property.

**Expect** — detached; the property still exists in the workspace list.

**Tools** `workitem_property:manage_type_properties` · **calls ≤ 3**

---

## F. Guards that must still hold

Regression cover — these worked before and must not have broken.

### 21. must fail — invalid PQL returns the reference

> List Regress work items with the PQL filter `stat = "done"`.

**Expect** — refused, with the PQL reference attached so the agent can correct itself.

**Also try** `nonexistent_field = "x"` — that returns a different error shape from Plane
(`code: invalid_filter_field`) and must **also** carry the reference. That second shape was
previously dropped on the floor.

**Tools** `workitem:list` · **calls ≤ 2**

### 22. must fail — invalid enum values

> Create a work item in Regress with priority 'sooner-than-urgent'.

**Expect** — refused, naming the allowed priorities. An item created with the priority
silently dropped is the bug.

**Tools** `workitem:create` · **calls ≤ 2**

### 23. should work — retired tool names still resolve

If your client can call a tool by name directly:

> Call `count_work_items` with pql `priority = "urgent"`.
> Then call `retrieve_work_item` with the project id and a work item id.

**Expect** — both work, and `retrieve_work_item` still takes **`work_item_id`**, its
original spelling. 169 retired names resolve this way; argument validation deliberately
skips them.

**Tools** retired aliases · **calls ≤ 2**

### 24. should work — a stringified array is repaired

> Assign Urgent A to me, passing the assignees as the JSON string `["<your user id>"]`.

**Expect** — works. The server decodes it before validation.

**Tools** `workitem:manage_assignee` · **calls ≤ 3**

---

## G. Known gaps — do not report these as new

Confirmed still broken. Listed so a tester does not spend time on them.

| | What happens | Layer |
| --- | --- | --- |
| **Add a state called 'Blocked' to Regress** | Refused: `This resource is managed at the workspace level`, with no alternative — the surface has no workspace-scoped state actions. Fix is ready, not applied. | ours |
| **Set labels on a work item using a label from another project** | 200 OK, the label is silently dropped, **and existing labels are removed**. Issue #193's failure mode, still live for labels. | api |
| **Create a type called Epic** | Comes back `is_epic: false` — named Epic, not treated as one. | api |
| **Add three assignees who are not project members** | Refused correctly by name, but no tool can add project members, so there is no way forward. | api / sdk |
| **Retrieve work item `00000000-0000-0000-0000-000000000000`** | `403 Forbidden` rather than a not-found. | api |
| **A two-week cycle** | Stored range comes back ~15 days; after `complete`, `end_date` sorts before `start_date`. | api |
| **Pretty-printed `description_html`** | Blank lines and one-tag-per-line lists render as phantom empty paragraphs and bullets. No normalization anywhere. | ours / api |

---

## Finish

> Delete the Regress project.

Then report:

1. Total tool calls for the run.
2. Any prompt in **A–F** that failed, with the error text.
3. Any prompt where the agent needed a nudge.
4. Any error that was technically correct but left the agent stuck.

Sections **A–F** should produce **zero unintended errors**. The only refusals should be
prompts 3, 11, 21 and 22, which are refusals by design — and each of those should say what
to do instead.
