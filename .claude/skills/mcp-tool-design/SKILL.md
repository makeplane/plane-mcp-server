---
name: mcp-tool-design
description: Design and implement MCP tools for the Auto-K server. Use when creating new MCP tools, adding actions to existing tools, designing tool response formats, writing tool descriptions, or reviewing MCP tool ergonomics. Covers naming conventions, parameter design, compact display formats, error handling, response models, and registration patterns.
---

# MCP Tool Design Guide for Auto-K

Reference for designing, implementing, and reviewing MCP tools exposed to AI agents.

## Design Principles

1. **Token efficiency** — AI agents pay per token. Default to compact responses; full detail only on explicit request.
2. **Flat parameters** — No nested objects. AI models handle flat parameter lists better than complex structures.
3. **Progressive disclosure** — List (compact summaries) -> Detail (full content). Never dump everything.
4. **Actionable errors** — Every error tells the AI what to try next.
5. **Display IDs over UUIDs** — Human-readable IDs in all inputs and outputs.
6. **Only include what drives the next action** — If a field doesn't help the AI decide what to do next, drop it.
7. **Don't echo what the caller already knows** — If the AI just passed `artifact_id='US-1'`, don't repeat it back in the response.

---

## Naming Conventions

### Tool Names

- **snake_case** always: `search_graph`, `create_nodes`, `get_node_details`
- **Verb-noun** for single-purpose tools: `search_graph`, `edit_node`, `get_task_spec`
- **Noun with action dispatch** for CRUD groups: `project(action=...)`, `source(action=...)`, `comment(action=...)`

### When to Use Multi-Action vs Separate Tools

| Pattern               | When                                            | Example                                   |
| --------------------- | ----------------------------------------------- | ----------------------------------------- |
| Multi-action dispatch | Related CRUD on one entity type                 | `project(action="list"\|"set"\|"create")` |
| Separate tools        | Distinct operations with different param shapes | `search_graph` vs `get_node_details`      |

### Action Naming (for multi-action tools)

Use verbs that map to the operation:

- `list` / `get` — Read operations
- `create` — New entity
- `set` — Change active state
- `update` / `edit` — Modify existing
- `delete` — Remove
- `validate` — Check correctness

Compound actions are fine when they reduce ambiguity: `list_threads`, `get_thread`, `open_threads`, `add_dep`.

---

## Parameter Design

### Always Use Flat Parameters with Annotations

```python
@log_call("mcp.tool_name")
@mcp.tool
async def my_tool(
    ctx: Context,
    action: Annotated[
        Literal["list", "get", "create"],
        Field(description="Action to perform"),
    ],
    node_id: Annotated[
        str | None,
        Field(default=None, description="Node display ID, e.g. 'US-1' (required for 'get')"),
    ] = None,
    limit: Annotated[
        int,
        Field(default=25, ge=1, le=100, description="Max results (for 'list')"),
    ] = 25,
) -> SomeResponse:
    """One-line summary of what this tool does.

    Actions:
    - list: What list does
    - get: What get does (requires node_id)
    - create: What create does (requires name, content)

    Requires an active project - use project(action='set') first.
    """
```

### Parameter Rules

1. **`ctx: Context` always first** — For session state and auth
2. **`action` second** (for multi-action tools) — `Literal[...]` with all valid values
3. **Optional params use `= None`** — Both in the annotation and as default
4. **Descriptions mention which action needs the param** — e.g., `"(required for 'get')"`
5. **Range constraints via Field** — `ge=1, le=100` for limits, `max_length=...` for strings
6. **Display ID params** — Use `DisplayId` type or `str` with description showing format: `"e.g. 'US-1'"`

---

## Compact Display Formats

### Established Patterns

Every entity type that appears in tool responses needs a compact single-line format.

| Entity                      | Format                               | Example                                              |
| --------------------------- | ------------------------------------ | ---------------------------------------------------- |
| Node                        | `"ID Name (type, status)"`           | `"US-1 Login Feature (user_story, proposed)"`        |
| Node with pending promotion | `"ID Name (type, status -> target)"` | `"TASK-1 Build API (task, proposed -> implemented)"` |
| Edge                        | `"SOURCE --type--> TARGET"`          | `"PER-1 --acts--> US-1"`                             |

### Rules for New Compact Formats

1. **One line per item** — Must fit in a single string
2. **Most important info first** — ID, name/content preview, then metadata
3. **Parenthetical metadata last** — `(type, status)` pattern
4. **Truncate long text** — Preview at ~80-100 chars max, with `...`
5. **Use formatting helpers** — Put in `formatting.py`, reuse across tools

### Format Helper Pattern

```python
# In app/features/mcp/formatting.py
def format_node_compact(node, pending_promotions=None) -> str:
    """'ID Name (type, status)' format."""
    ...

def format_edge_compact(edge, id_to_display=None) -> str:
    """'SOURCE --type--> TARGET' format."""
    ...
```

---

## Response Models

### Location and Structure

All response models live in `app/features/mcp/models.py`. Group by tool category with section headers.

### Design Rules

1. **Lists use compact strings** — `nodes: list[str]` with compact format, not full objects
2. **Detail uses typed models** — `NodeDetails` with all fields
3. **Only include counts when the list is not returned** — If the AI can count the list, don't add `total_count`
4. **Pagination fields only for paginated endpoints** — `has_more` only when there's a `limit` param
5. **Optional expensive fields** — `visualization: str | None = None`, only populated when requested
6. **Docstrings show format** — Include example output in the model docstring

### Structuring Nested Data

When data is naturally hierarchical (e.g., threads containing comments), use **native JSON
structure** instead of formatted strings with `\n`:

```python
# BAD — crammed into a string with \n
threads: list[str]
# "T1 (open, v1, 2 comments)\n  user@email (2h ago): comment...\n  AI (1h ago): reply..."

# GOOD — native structure, AI parses JSON natively
threads: dict[str, list[str]]
# {"T1": ["user@email: comment...", "AI: reply..."], "T2": ["user@email: ..."]}
```

Rules for nested responses:

- **Use dict keys for refs** — `{"T1": [...], "T2": [...]}` not a flat list with embedded labels
- **Each list item is one logical entry** — One comment per string, one node per string
- **No metadata the AI won't act on** — Drop timestamps, counts, versions unless they change behavior
- **Author attribution is content, not metadata** — `"user@email: text"` or `"AI: text"` inline

### What to Strip from Responses

Ask for each field: "Does this help the AI decide what to do next?"

| Field            | Keep?                  | Why                                                    |
| ---------------- | ---------------------- | ------------------------------------------------------ |
| Display ID / ref | Yes                    | Needed to drill down or reference                      |
| Content / name   | Yes                    | The actual information                                 |
| Author           | Yes                    | Distinguishes human from AI                            |
| Status           | Only if mixed          | If only open items are returned, status is redundant   |
| Timestamps       | Rarely                 | AI doesn't usually care when something was said        |
| Counts           | Only if list is absent | If the list is right there, the AI can count it        |
| Version          | Rarely                 | Only if multi-version context matters for the action   |
| Type annotations | Only if actionable     | "inline" matters (has anchor text), "artifact" doesn't |

---

## Error Handling

### Always Use ToolError

```python
from app.features.mcp.errors import ToolError

raise ToolError(
    "validation",                           # Short error code
    "Too many nodes (51)",                  # What went wrong
    "Split into multiple create_nodes calls with max 50 nodes each",  # What to try next
)
```

### Error Codes

| Code           | When                                         |
| -------------- | -------------------------------------------- |
| `validation`   | Bad input, out of range, wrong format        |
| `not_found`    | Entity doesn't exist                         |
| `no_project`   | No active project set                        |
| `conflict`     | State conflict (e.g., editing accepted node) |
| `unauthorized` | Permission denied                            |

### Recovery Instructions are Mandatory

Every `ToolError` **must** include a recovery hint. This is how the AI self-corrects:

- Bad: `"Invalid node ID"` (AI doesn't know what to do)
- Good: `"Node 'XYZ-99' not found"` + recovery: `"Use search_graph to find valid IDs"`

---

## Tool Registration

### File Structure

```text
src/app/features/mcp/tools/
    __init__.py          # Exports all register_* functions
    my_category.py       # One file per tool category
```

### Registration Pattern

```python
# my_category.py
def register_my_tools(mcp: FastMCP, container: Container) -> None:
    """Register my-category tools."""

    @log_call("mcp.my_tool")
    @mcp.tool
    async def my_tool(ctx: Context, ...) -> ResponseModel:
        """Docstring is the tool description shown to AI."""
        pctx = await utils.ensure_project_context(ctx, container)
        # ... implementation
```

### Registration in server.py

```python
# In create_mcp_server():
register_my_tools(mcp, container)
```

And add to `__init__.py`:

```python
from app.features.mcp.tools.my_category import register_my_tools
```

### Implementation Helpers

Extract action handlers into private `async def _action_name(...)` functions to keep the main tool function clean:

```python
@mcp.tool
async def my_tool(ctx, action, ...) -> Response:
    pctx = await utils.ensure_project_context(ctx, container)
    if action == "list":
        return await _list_items(container, pctx.project_id, limit)
    if action == "get":
        return await _get_item(container, pctx.project_id, item_id)
    raise ValueError(f"Unknown action: {action}")

async def _list_items(...) -> ListResponse:
    ...
```

---

## Logging

### Decorator on Every Public Tool

```python
@log_call("mcp.tool_name")           # Standard
@log_call("mcp.tool_name", log_start=True)  # Also log entry (for slow tools)
@log_call("mcp.tool_name", include_args=False)  # Bulk ops (avoid flooding)
```

### Log Call Convention

- Prefix: `mcp.` for all MCP tools
- Suffix: tool function name
- Examples: `mcp.project`, `mcp.search_graph`, `mcp.comment`

---

## Server Instructions

When adding a new tool category, update `_SERVER_INSTRUCTIONS` in `server.py`:

```python
_SERVER_INSTRUCTIONS = """
...
## Tool Categories
...
- **comments**: `comment` - Read and respond to discussions on artifacts
  (list_threads, get_thread, open_threads, reply, resolve)
...
"""
```

Also update `docs/agent-tools.md` with the tool reference.

---

## Docstring Conventions

The tool's docstring **is** the description shown to AI clients. Make it count.

### Structure

```text
Line 1: One-sentence summary of what the tool does.

Actions: (for multi-action tools)
- action1: What it does (requires param_x)
- action2: What it does

Extra context for effective usage.

Requires an active project - use project(action='set') first.
```

### Rules

1. **First line is the most important** — AI clients may only show this
2. **List all actions with required params** — AI needs to know what params each action needs
3. **End with prerequisites** — "Requires an active project" if applicable
4. **Show examples in descriptions for complex params** — e.g., `"e.g. 'US-1'"`

---

## Response Serialization: Strings vs Structured JSON

MCP tool results are serialized as JSON. This affects how humans see responses in transcripts:

- **Returning `str`** → JSON-escaped: `{"result": "# Title\n\nLine 2\n- item"}` — newlines become `\n` literals. Unreadable for humans, fine for AI.
- **Returning `dict` / Pydantic model** → Pretty-printed JSON with real line breaks. Readable for both.

### Decision: Prefer compact strings

Structured JSON adds ~25-30% token overhead from keys, braces, quotes, and indentation. Since the primary consumer is the AI model (not the human reading the transcript), **default to compact strings** for token efficiency.

Use structured responses only when:

- The data is naturally tabular and the AI needs to reference specific fields programmatically
- The response is a detail view with many distinct fields (`SourceDetailResponse`, `NodeDetails`)
- Nested data would be unreadable as a flat string (comment threads → `dict[str, list[str]]`)

### Content files need frontmatter

Content library files (workflows, templates, guides) must have YAML frontmatter with `name` and `description` fields. Without frontmatter, `browse_content()` renders empty descriptions:

```markdown
---
name: prd-generation
description: Build a product knowledge graph through stakeholder interviews
---

# PRD Generation Workflow

...
```

---

## Short UUIDs for Non-Graph Entities

Graph nodes use display IDs (`US-1`, `REQ-3`). Non-graph entities (sources, etc.) use UUIDs internally but expose **short UUIDs** via `shortuuid.encode()` in MCP responses.

```python
import shortuuid

def _short_id(uuid: UUID) -> str:
    return shortuuid.encode(uuid)

def _resolve_source_id(source_id: str) -> UUID:
    """Accept both short and full UUIDs."""
    try:
        return UUID(source_id)
    except ValueError:
        return shortuuid.decode(source_id)
```

- Deterministic and reversible — same UUID always produces same short code
- Tools must accept both formats as input (try `UUID()` first, fall back to `shortuuid.decode()`)
- Compact format: `"4rBqhfwQBKowkBLP2XAKkJ: filename.md (text/markdown, uploaded)"`

---

## Checklist for New Tools

Before submitting a new MCP tool:

- [ ] Tool name follows snake_case naming convention
- [ ] Parameters are flat (no nested objects)
- [ ] All params use `Annotated[..., Field(description=...)]`
- [ ] Response model defined in `models.py` with docstring showing format
- [ ] Compact display format defined for any new entity type
- [ ] `@log_call("mcp.tool_name")` decorator applied
- [ ] Uses `ensure_project_context(ctx, container)` (if project-scoped)
- [ ] All errors use `ToolError(code, message, recovery)`
- [ ] Registration function added to `__init__.py` and `server.py`
- [ ] `_SERVER_INSTRUCTIONS` updated with new tool category
- [ ] `docs/agent-tools.md` updated with tool reference
- [ ] Tests added in `src/tests/integration/mcp/`
