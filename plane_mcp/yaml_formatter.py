"""YAML output formatter for Plane MCP servers (Admin, Agent, Docs).

Converts dict/list tool responses into a compact, LLM-friendly YAML format.

Design decisions:
- Lists use flow-style (inline) to save tokens: [foo, bar, baz]
- Multiline strings use literal block scalar (|) for readability
- None, empty string, and empty list/dict values are stripped
- 2-space indentation (YAML default)
- No line-wrapping (width=1000) to prevent arbitrary breaks
- Values containing colons are forced-quoted for parser safety
- Values containing commas or spaces are auto-quoted by PyYAML
- Anchors and aliases (&id001 / *id001) are completely suppressed via ignore_aliases

Missing-field semantics:
    Fields with None, "", [], or {} values are omitted from output.
    Consumers should treat a missing field as empty/unset.
    Falsy-but-meaningful values (False, 0) are preserved.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

import yaml
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Callable[..., Any])


class FlowList(list):
    """A list subclass that YAML serializes in flow style: [a, b, c]."""

    pass


class InlineSafeDumper(yaml.SafeDumper):
    """Custom SafeDumper that suppresses anchors/aliases and supports FlowList and literal blocks."""

    def ignore_aliases(self, data: Any) -> bool:
        """Suppress YAML anchors and aliases (&id001 / *id001) for all nodes."""
        return True


def _flow_list_representer(dumper: yaml.SafeDumper, data: FlowList) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    """Use literal block scalar for multiline strings.

    Forces single-quoting for values containing colons or dashes to prevent
    YAML parser ambiguity.
    """
    if len(data.splitlines()) > 1:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    if ":" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


InlineSafeDumper.add_representer(FlowList, _flow_list_representer)
InlineSafeDumper.add_representer(str, _str_presenter)
# Also register on global SafeDumper for compatibility if external yaml.safe_dump is called
yaml.SafeDumper.add_representer(FlowList, _flow_list_representer)
yaml.SafeDumper.add_representer(str, _str_presenter)


def _strip_control_chars(text: str) -> str:
    """Strip dangerous ASCII control characters while keeping newline, carriage return, and tab."""
    return "".join(c for c in text if c in ("\n", "\r", "\t") or ord(c) >= 32)


def _clean_and_convert(obj: Any) -> Any:
    """Recursively strip empty values and convert lists to FlowList.

    Removes: None, "", [], {}
    Preserves: False, 0, and other falsy-but-meaningful values.
    """
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    elif hasattr(obj, "dict") and callable(obj.dict):
        obj = obj.dict()

    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            val = _clean_and_convert(v)
            if val not in (None, "", [], {}):
                clean_k = _strip_control_chars(str(k)) if isinstance(k, str) else k
                cleaned[clean_k] = val
        return cleaned
    elif isinstance(obj, (list, tuple)):
        cleaned_list = [_clean_and_convert(v) for v in obj]
        # Filter out empty elements
        filtered = [v for v in cleaned_list if v not in (None, "", [], {})]
        # Only use FlowList if all elements are primitives (inline array)
        if all(isinstance(x, (str, int, float, bool)) for x in filtered):
            return FlowList(filtered)
        return filtered
    elif isinstance(obj, str):
        return _strip_control_chars(obj)
    return obj


def format_as_yaml(raw_data: Any) -> Any:
    """Convert a dict, list, Pydantic model, or JSON string to compact YAML string."""
    if isinstance(raw_data, str):
        stripped = raw_data.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, (dict, list)):
                    raw_data = parsed
            except Exception:
                pass

    if not isinstance(raw_data, (dict, list)) and not hasattr(raw_data, "model_dump"):
        return raw_data

    try:
        cleaned_data = _clean_and_convert(raw_data)
        if not cleaned_data and cleaned_data != 0 and cleaned_data is not False:
            return "{}"
        yaml_str = yaml.safe_dump(
            cleaned_data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=1000,
        )
        return yaml_str.strip()
    except Exception as e:
        logger.debug("YAML formatting failed: %s", e)
        return raw_data


def with_yaml(func: T) -> T:
    """Decorator that converts a tool's dict/list return value to YAML."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        return format_as_yaml(result)

    return cast(T, wrapper)


class YamlOutputMiddleware(Middleware):
    """FastMCP Middleware that formats all tool call results as YAML across surfaces."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        result = await call_next(context)
        if isinstance(result, ToolResult):
            new_content = []
            for block in result.content:
                if isinstance(block, TextContent):
                    new_content.append(TextContent(type="text", text=format_as_yaml(block.text)))
                else:
                    new_content.append(block)
            return ToolResult(
                content=new_content,
                structured_content=None,  # Strip duplicate JSON structuredContent
                meta=result.meta,
            )
        return ToolResult(content=[TextContent(type="text", text=format_as_yaml(result))])
