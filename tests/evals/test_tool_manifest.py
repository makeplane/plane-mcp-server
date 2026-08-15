"""Tool-manifest fingerprint regression tests."""

from __future__ import annotations

from evals.tool_manifest import ToolManifestCapture, tool_manifest_fingerprint


def test_same_tool_names_with_different_schemas_have_different_manifest_fingerprints():
    short = [
        {
            "name": "create_work_item",
            "description": "Create an item",
            "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}},
        }
    ]
    consolidated = [
        {
            "name": "create_work_item",
            "description": "Create an item",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace_slug": {"type": "string"},
                    "project_slug": {"type": "string"},
                    "title": {"type": "string"},
                    "type_id": {"type": "string"},
                },
            },
        }
    ]

    assert tool_manifest_fingerprint(short) != tool_manifest_fingerprint(consolidated)


def test_paginated_tools_list_hashes_like_equivalent_single_page():
    first = [{"name": "alpha", "inputSchema": {"type": "object"}}]
    second = [{"name": "beta", "inputSchema": {"type": "object"}}]
    paginated = ToolManifestCapture()
    paginated.observe_page({"tools": first, "nextCursor": "page-2"}, request_cursor=None)
    assert paginated.fingerprint is None
    paginated.observe_page({"tools": second}, request_cursor="page-2")

    single = ToolManifestCapture()
    single.observe_page({"tools": [*second, *first]}, request_cursor=None)

    assert paginated.fingerprint == single.fingerprint


def test_manifest_fingerprint_recursively_canonicalizes_object_key_order():
    left = [
        {
            "name": "lookup",
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string", "description": "query"}},
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }
    ]
    right = [
        {
            "annotations": {"destructiveHint": False, "readOnlyHint": True},
            "inputSchema": {
                "properties": {"q": {"description": "query", "type": "string"}},
                "type": "object",
            },
            "name": "lookup",
        }
    ]

    assert tool_manifest_fingerprint(left) == tool_manifest_fingerprint(right)
