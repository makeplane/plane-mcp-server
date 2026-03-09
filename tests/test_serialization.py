"""Unit tests for the deserialize_list_param utility.

These tests verify the fix for Issue #58: FastMCP serializes array parameters
as JSON strings instead of Python lists, causing Pydantic v2 validation failures.
"""

from plane_mcp.utils.serialization import deserialize_list_param


class TestDeserializeListParam:
    """Tests for deserialize_list_param()."""

    def test_none_returns_none(self):
        assert deserialize_list_param(None) is None

    def test_list_passes_through(self):
        val = ["uuid1", "uuid2"]
        assert deserialize_list_param(val) == ["uuid1", "uuid2"]

    def test_empty_list_passes_through(self):
        assert deserialize_list_param([]) == []

    def test_json_string_list_is_deserialized(self):
        """This is the core bug: FastMCP sends '["uuid1", "uuid2"]' as a string."""
        val = '["uuid1", "uuid2"]'
        result = deserialize_list_param(val)
        assert result == ["uuid1", "uuid2"]
        assert isinstance(result, list)

    def test_json_string_empty_list(self):
        assert deserialize_list_param("[]") == []

    def test_json_string_single_item(self):
        val = '["only-one"]'
        assert deserialize_list_param(val) == ["only-one"]

    def test_json_string_with_dicts(self):
        """For options params that are list[dict]."""
        val = '[{"name": "opt1", "value": "v1"}]'
        result = deserialize_list_param(val)
        assert result == [{"name": "opt1", "value": "v1"}]
        assert isinstance(result, list)

    def test_non_list_json_string_returned_unchanged(self):
        """A JSON string that parses to a dict (not list) should pass through."""
        val = '{"key": "value"}'
        assert deserialize_list_param(val) == '{"key": "value"}'

    def test_plain_string_returned_unchanged(self):
        """A non-JSON string should be returned as-is."""
        assert deserialize_list_param("hello") == "hello"

    def test_invalid_json_returned_unchanged(self):
        assert deserialize_list_param("[invalid json") == "[invalid json"

    def test_integer_returned_unchanged(self):
        assert deserialize_list_param(42) == 42

    def test_boolean_returned_unchanged(self):
        assert deserialize_list_param(True) is True

    def test_nested_list(self):
        val = '[["a", "b"], ["c"]]'
        result = deserialize_list_param(val)
        assert result == [["a", "b"], ["c"]]
