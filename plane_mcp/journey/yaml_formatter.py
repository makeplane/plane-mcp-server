"""YAML output formatter for Journey tools.

Converts dict/list tool responses into a compact, LLM-friendly YAML format.

Design decisions:
- Lists use flow-style (inline) to save tokens: [foo, bar, baz]
- Multiline strings use literal block scalar (|) for readability
- None, empty string, and empty list/dict values are stripped
- 2-space indentation (YAML default)
- No line-wrapping (width=1000) to prevent arbitrary breaks
- Values containing colons are forced-quoted for parser safety
- Values containing commas or spaces are auto-quoted by PyYAML

Missing-field semantics:
    Fields with None, "", [], or {} values are omitted from output.
    Consumers should treat a missing field as empty/unset.
    Falsy-but-meaningful values (False, 0) are preserved.

Behavioral contracts:
- Input: any dict, list, or primitive returned by a Journey tool
- Output: YAML string for dict/list inputs; passthrough for primitives
- Failure mode: on any exception, returns original data unchanged (no data loss)
"""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

import yaml

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Callable[..., Any])


class FlowList(list):
    """A list subclass that YAML serializes in flow style: [a, b, c].

    PyYAML's SafeDumper will render normal lists as block-style (- item).
    By registering a custom representer for this subclass, we get inline
    arrays that are more token-efficient for LLM consumption.
    """

    pass


def _flow_list_representer(dumper: yaml.SafeDumper, data: FlowList) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    """Use literal block scalar for multiline strings.

    Forces single-quoting for values containing colons to prevent
    YAML parser ambiguity (e.g. cursor values like '3:-1:1').
    """
    if len(data.splitlines()) > 1:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    if ":" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.SafeDumper.add_representer(FlowList, _flow_list_representer)
yaml.SafeDumper.add_representer(str, _str_presenter)


def _clean_and_convert(obj: Any) -> Any:
    """Recursively strip empty values and convert lists to FlowList.

    Removes: None, "", [], {}
    Preserves: False, 0, and other falsy-but-meaningful values.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            val = _clean_and_convert(v)
            if val not in (None, "", [], {}):
                cleaned[k] = val
        return cleaned
    elif isinstance(obj, list):
        cleaned_list = [_clean_and_convert(v) for v in obj]
        is_primitive = all(not isinstance(item, (dict, list)) for item in cleaned_list)
        if is_primitive:
            return FlowList(cleaned_list)
        return cleaned_list
    return obj


def format_as_yaml(raw_data: Any) -> Any:
    """Convert a dict or list to compact YAML string.

    Args:
        raw_data: Tool output (typically dict or list).

    Returns:
        YAML string if input is dict/list, original value otherwise.
        On formatting failure, returns original data unchanged.
    """
    if not isinstance(raw_data, (dict, list)):
        return raw_data

    try:
        cleaned_data = _clean_and_convert(raw_data)
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
    """Decorator that converts a tool's dict/list return value to YAML.

    Must be applied OUTSIDE (above) mcp_error_boundary so that error
    dicts also get YAML-formatted. Stack order:

        @mcp.tool()
        @with_yaml
        @mcp_error_boundary
        def my_tool(...) -> dict: ...
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        return format_as_yaml(result)

    if 'return' in wrapper.__annotations__:
        wrapper.__annotations__['return'] = Any

    return cast(T, wrapper)
