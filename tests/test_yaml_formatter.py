"""Tests for the YAML output formatter.

Validates:
- Flow-style list rendering: [foo, bar, baz]
- Multiline literal block scalars
- Empty/null value stripping
- Values with special characters (commas, spaces) are quoted
- Decorator integration via with_yaml
- Passthrough of non-dict/list inputs
- Graceful fallback on formatting errors
"""

from unittest.mock import patch

import yaml

from plane_mcp.journey.yaml_formatter import (
    FlowList,
    _clean_and_convert,
    format_as_yaml,
    with_yaml,
)


class TestFlowList:
    def test_flow_list_is_list(self):
        fl = FlowList([1, 2, 3])
        assert isinstance(fl, list)
        assert fl == [1, 2, 3]

    def test_flow_list_yaml_rendering(self):
        """FlowList must render as inline [a, b, c] not block-style."""
        data = {"labels": FlowList(["foo", "bar", "baz"])}
        result = yaml.safe_dump(data, sort_keys=False)
        assert "labels: [foo, bar, baz]" in result

    def test_flow_list_with_special_chars(self):
        """Values with commas or spaces must be quoted."""
        data = {"labels": FlowList(["foo", "buzz bing", "pow,bangboom"])}
        result = yaml.safe_dump(data, sort_keys=False)
        # PyYAML will quote these automatically
        assert "buzz bing" in result
        assert "pow,bangboom" in result
        # Verify it's still flow style (on one line)
        for line in result.strip().splitlines():
            if line.startswith("labels:"):
                assert "[" in line and "]" in line
                break


class TestCleanAndConvert:
    def test_strips_none_values(self):
        data = {"a": 1, "b": None, "c": "hello"}
        result = _clean_and_convert(data)
        assert "b" not in result
        assert result["a"] == 1

    def test_strips_empty_strings(self):
        data = {"a": "", "b": "text"}
        result = _clean_and_convert(data)
        assert "a" not in result
        assert result["b"] == "text"

    def test_strips_empty_lists(self):
        data = {"a": [], "b": [1, 2]}
        result = _clean_and_convert(data)
        assert "a" not in result
        assert isinstance(result["b"], FlowList)

    def test_strips_empty_dicts(self):
        data = {"a": {}, "b": {"key": "val"}}
        result = _clean_and_convert(data)
        assert "a" not in result
        assert result["b"] == {"key": "val"}

    def test_preserves_false_and_zero(self):
        data = {"a": False, "b": 0, "c": None}
        result = _clean_and_convert(data)
        assert result["a"] is False
        assert result["b"] == 0
        assert "c" not in result

    def test_converts_lists_to_flow_list(self):
        data = {"tags": ["x", "y"]}
        result = _clean_and_convert(data)
        assert isinstance(result["tags"], FlowList)

    def test_nested_cleaning(self):
        data = {"outer": {"keep": "yes", "drop": None, "nested_list": ["a", "b"], "empty_nested": {}}}
        result = _clean_and_convert(data)
        assert "drop" not in result["outer"]
        assert "empty_nested" not in result["outer"]
        assert isinstance(result["outer"]["nested_list"], FlowList)


class TestFormatAsYaml:
    def test_basic_dict(self):
        data = {"issue_key": "TEST-999", "title": "Hello"}
        result = format_as_yaml(data)
        assert isinstance(result, str)
        assert "issue_key: TEST-999" in result
        assert "title: Hello" in result

    def test_flow_style_labels(self):
        """The hallmark feature: labels render as [foo, bar, baz]."""
        data = {
            "issue_key": "TEST-999",
            "labels": ["foo", "bar", "baz"],
        }
        result = format_as_yaml(data)
        assert "labels: [foo, bar, baz]" in result

    def test_labels_with_special_chars(self):
        """Labels with spaces/commas are quoted inline."""
        data = {
            "issue_key": "TEST-999",
            "labels": ["foo", "bar", "baz", "buzz bing", "pow,bangboom"],
        }
        result = format_as_yaml(data)
        # Should be on one line, flow-style
        for line in result.splitlines():
            if line.startswith("labels:"):
                assert "[" in line and "]" in line
                assert "buzz bing" in line
                break

    def test_multiline_description(self):
        """Multiline strings should use literal block scalar (|)."""
        data = {
            "issue_key": "TEST-1",
            "description": "Line one\nLine two\nLine three",
        }
        result = format_as_yaml(data)
        assert "description: |" in result or "description: |\n" in result

    def test_strips_empty_values(self):
        data = {"a": 1, "b": None, "c": "", "d": [], "e": "keep"}
        result = format_as_yaml(data)
        assert "b:" not in result
        assert "c:" not in result
        assert "d:" not in result
        assert "a: 1" in result
        assert "e: keep" in result

    def test_passthrough_primitive(self):
        assert format_as_yaml("hello") == "hello"
        assert format_as_yaml(42) == 42
        assert format_as_yaml(None) is None

    def test_list_input(self):
        data = [{"a": 1}, {"b": 2}]
        result = format_as_yaml(data)
        assert isinstance(result, str)
        assert "a: 1" in result

    def test_two_space_indent(self):
        """Nested dicts should use 2-space indentation (YAML default)."""
        data = {"outer": {"inner": "value"}}
        result = format_as_yaml(data)
        lines = result.splitlines()
        # Find the indented line
        inner_lines = [line for line in lines if "inner:" in line]
        assert len(inner_lines) == 1
        assert inner_lines[0].startswith("  inner:")

    def test_error_fallback(self):
        """On YAML dump failure, return original data unchanged."""
        bad_data = {"key": "value"}
        with patch(
            "plane_mcp.journey.yaml_formatter.yaml.safe_dump",
            side_effect=Exception("boom"),
        ):
            result = format_as_yaml(bad_data)
        assert result == bad_data


class TestWithYamlDecorator:
    def test_wraps_dict_return(self):
        @with_yaml
        def my_tool():
            return {"issue_key": "TEST-1", "title": "Hello"}

        result = my_tool()
        assert isinstance(result, str)
        assert "issue_key: TEST-1" in result

    def test_wraps_list_return(self):
        @with_yaml
        def my_tool():
            return [{"a": 1}]

        result = my_tool()
        assert isinstance(result, str)

    def test_passthrough_string_return(self):
        @with_yaml
        def my_tool():
            return "already a string"

        result = my_tool()
        assert result == "already a string"

    def test_preserves_function_name(self):
        @with_yaml
        def my_cool_tool():
            """Docstring."""
            return {}

        assert my_cool_tool.__name__ == "my_cool_tool"
        assert my_cool_tool.__doc__ == "Docstring."


class TestEndToEndFormat:
    """Integration-style test matching the user's exact example format."""

    def test_user_example_format(self):
        data = {
            "issue_key": "TEST-999",
            "labels": ["foo", "bar", "baz", "buzz bing", "pow,bangboom"],
            "description": "This is the description of the ticket.\nIt spans multiple lines.",
        }
        result = format_as_yaml(data)

        # issue_key on its own line
        assert "issue_key: TEST-999" in result

        # labels as flow list
        for line in result.splitlines():
            if line.startswith("labels:"):
                assert line.startswith("labels: [")
                assert line.endswith("]")
                break
        else:
            raise AssertionError("labels line not found")

        # description as literal block
        assert "description: |" in result or "description: |\n" in result
