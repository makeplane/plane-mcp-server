"""Variant B: non-breaking JSON-Schema compression.

Two transforms, both purely structural — the schema keeps the same meaning, so
clients that consume `structuredContent` are unaffected:

1. `anyOf: [{type: X}, {type: "null"}]`  ->  `{type: [X, "null"]}`
   Pydantic v2 emits the verbose form for every `X | None = None` field.

2. Repeated identical subschemas are hoisted into `$defs` and replaced by
   `$ref`. FastMCP inlines nested models at every occurrence, so a model like
   `WorkItem` can appear in full many times inside one schema.
"""

from __future__ import annotations

import json
from typing import Any

# Subschemas smaller than this are cheaper inline than as a $ref indirection.
_MIN_HOIST_CHARS = 120


def _collapse_nullable(node: Any) -> Any:
    """Recursively rewrite anyOf-with-null into a type array."""
    if isinstance(node, list):
        return [_collapse_nullable(n) for n in node]
    if not isinstance(node, dict):
        return node

    node = {k: _collapse_nullable(v) for k, v in node.items()}

    variants = node.get("anyOf")
    if not isinstance(variants, list) or len(variants) != 2:
        return node

    nulls = [v for v in variants if isinstance(v, dict) and v.get("type") == "null"]
    others = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
    if len(nulls) != 1 or len(others) != 1:
        return node

    other = others[0]
    # Only safe to fold when the non-null branch is a bare scalar type; a nested
    # object branch keeps its anyOf (the $ref pass will shrink it instead).
    if set(other.keys()) == {"type"} and isinstance(other["type"], str):
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        return {"type": [other["type"], "null"], **rest}
    return node


def _walk(node: Any, fn) -> Any:
    if isinstance(node, list):
        return [_walk(n, fn) for n in node]
    if isinstance(node, dict):
        return fn({k: _walk(v, fn) for k, v in node.items()})
    return node


def _key(node: Any) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"))


def _hoistable(node: Any) -> bool:
    return isinstance(node, dict) and ("properties" in node or "enum" in node)


def _dedupe_refs(schema: dict) -> dict:
    """Hoist repeated subschemas into $defs and replace them with $ref.

    Counting is done on the original tree, but substitution must run *top-down*
    so that an outer repeated model wins before its children are rewritten. A
    bottom-up pass rewrites children first, which changes every ancestor's
    serialization so no ancestor ever matches its tallied key -- the result is
    that only leaf models dedupe while full-size bodies still land in $defs,
    and the schema grows instead of shrinking.
    """
    counts: dict[str, int] = {}

    def tally(node: Any) -> Any:
        if _hoistable(node):
            key = _key(node)
            if len(key) >= _MIN_HOIST_CHARS:
                counts[key] = counts.get(key, 0) + 1
        return node

    _walk(schema, tally)

    repeated = {k: v for k, v in counts.items() if v > 1}
    if not repeated:
        return schema

    # Name largest-total-saving first: T1 is the biggest win.
    names = {
        key: f"T{i}"
        for i, key in enumerate(sorted(repeated, key=lambda k: -len(k) * repeated[k]), start=1)
    }

    def substitute(node: Any, skip_root: bool = False) -> Any:
        """Top-down: replace a hoistable node with its $ref and stop descending."""
        if isinstance(node, list):
            return [substitute(n) for n in node]
        if not isinstance(node, dict):
            return node
        if not skip_root and _hoistable(node):
            key = _key(node)
            if key in names:
                return {"$ref": f"#/$defs/{names[key]}"}
        return {k: substitute(v) for k, v in node.items()}

    # Each hoisted body is itself substituted (skipping its own root) so nested
    # repeated models collapse inside $defs too.
    defs = {name: substitute(json.loads(key), skip_root=True) for key, name in names.items()}
    out = substitute(schema, skip_root=True)
    out["$defs"] = {**out.get("$defs", {}), **defs}
    return out


def compress(schema: dict | None) -> dict | None:
    """Apply both transforms. Returns a semantically equivalent schema."""
    if not schema:
        return schema
    return _dedupe_refs(_collapse_nullable(schema))
