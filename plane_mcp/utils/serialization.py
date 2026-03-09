"""Serialization utilities for handling FastMCP parameter quirks.

FastMCP serializes array parameters as JSON strings (e.g., '["uuid1", "uuid2"]')
instead of Python lists, causing Pydantic v2 validation failures. This module
provides utilities to normalize these parameters before they reach Pydantic models.
"""

from __future__ import annotations

import json
from typing import Any


def deserialize_list_param(value: Any) -> Any:
    """Deserialize a value that may be a JSON-encoded list string.

    FastMCP can serialize list parameters as JSON strings instead of native Python
    lists. This function normalizes such values back into lists before they are
    passed to Pydantic models.

    Args:
        value: The parameter value to deserialize. Can be a JSON string encoding a
            list, an already-parsed list, None, or any other type.

    Returns:
        A Python list if the input was a JSON-encoded list string or already a list,
        None if the input was None, or the original value unchanged for any other type
        or if JSON parsing fails.
    """
    if value is None:
        return None

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return value
