"""Canonical, route-agnostic fingerprints for advertised MCP tool manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


def _json_value(value: Any) -> Any:
    """Return a stable JSON-compatible representation, omitting null object fields."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(by_alias=True, exclude_none=True)
    elif not isinstance(value, (dict, list, tuple, str, int, float, bool)) and value is not None:
        try:
            value = vars(value)
        except TypeError:
            value = str(value)

    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def canonical_tool_descriptors(tools: list[Any]) -> list[dict[str, Any]]:
    """Canonicalize complete advertised descriptors and sort them by tool name."""
    descriptors: list[dict[str, Any]] = []
    for tool in tools:
        descriptor = _json_value(tool)
        if not isinstance(descriptor, dict):
            descriptor = {"name": str(getattr(tool, "name", "")), "descriptor": descriptor}
        descriptors.append(descriptor)
    return sorted(
        descriptors,
        key=lambda item: (
            str(item.get("name") or ""),
            json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        ),
    )


def tool_manifest_fingerprint(tools: list[Any]) -> str:
    """Hash the complete canonical advertised tool descriptors."""
    payload = json.dumps(
        canonical_tool_descriptors(tools),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tools_page(page: Any) -> tuple[list[Any], str | None]:
    """Extract the advertised tools and next cursor from a tools/list result page."""
    if isinstance(page, dict):
        raw_tools = page.get("tools")
        cursor = page.get("nextCursor", page.get("next_cursor"))
    else:
        raw_tools = getattr(page, "tools", None)
        cursor = getattr(page, "nextCursor", None)
        if cursor is None:
            cursor = getattr(page, "next_cursor", None)
    tools = list(raw_tools) if isinstance(raw_tools, (list, tuple)) else []
    return tools, str(cursor) if cursor is not None else None


@dataclass(slots=True)
class ToolManifestCapture:
    """Aggregate one passive paginated tools/list snapshot."""

    descriptors: list[Any] = field(default_factory=list)
    expected_cursor: str | None = None
    active: bool = False
    fingerprint: str | None = None

    def observe_page(self, page: Any, *, request_cursor: str | None) -> None:
        """Observe one response page; publish only a complete root-to-final snapshot."""
        if request_cursor is None:
            self.descriptors = []
            self.expected_cursor = None
            self.active = True
            self.fingerprint = None
        elif not self.active or request_cursor != self.expected_cursor:
            self.invalidate()
            return

        tools, next_cursor = tools_page(page)
        self.descriptors.extend(tools)
        self.expected_cursor = next_cursor
        if next_cursor is None:
            self.fingerprint = tool_manifest_fingerprint(self.descriptors)
            self.active = False

    def invalidate(self) -> None:
        """Drop a partial or stale snapshot."""
        self.descriptors = []
        self.expected_cursor = None
        self.active = False
        self.fingerprint = None


__all__ = [
    "ToolManifestCapture",
    "canonical_tool_descriptors",
    "tool_manifest_fingerprint",
    "tools_page",
]
