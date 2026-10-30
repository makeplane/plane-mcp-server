"""Negative and adversarial security unit tests."""

import pytest
import yaml

from plane_mcp.resolver import validate_identifier
from plane_mcp.sanitize import apply_snippet_edits, sanitize_html
from plane_mcp.yaml_formatter import format_as_yaml


def test_validate_identifier_rejections():
    # Null bytes
    with pytest.raises(ValueError, match="invalid control characters or null bytes"):
        validate_identifier("ENG" + chr(0) + "1", "ticket_id")

    # Control characters
    with pytest.raises(ValueError, match="invalid control characters or null bytes"):
        validate_identifier("ENG\x1b1", "ticket_id")

    # Path traversal
    with pytest.raises(ValueError, match="path traversal sequence"):
        validate_identifier("../../etc/passwd", "path")

    # Empty / whitespace
    with pytest.raises(ValueError, match="cannot be empty or whitespace only"):
        validate_identifier("   ", "field")

    # Oversized
    with pytest.raises(ValueError, match="exceeds maximum length"):
        validate_identifier("A" * 200, "slug", max_len=50)


def test_sanitize_html_xss_protection():
    dirty = '<script>alert(1)</script><img src=x onerror="alert(2)"><a href="javascript:alert(3)">link</a>'
    clean = sanitize_html(dirty)
    assert "<script>" not in clean
    assert "onerror" not in clean
    assert "javascript:" not in clean
    assert "alert" not in clean or clean == "link"


def test_snippet_editing_rejections():
    current_html = "<p>Task A: Todo</p><p>Task B: Done</p>"

    # Missing snippet
    _, err = apply_snippet_edits(current_html, replace_target_snippet="Task C", replace_text="Task C: In Progress")
    assert err and "Target snippet not found" in err

    # Ambiguous snippet
    ambiguous_html = "<p>Task: 1</p><p>Task: 2</p>"
    _, err = apply_snippet_edits(ambiguous_html, replace_target_snippet="Task:", replace_text="Job:")
    assert err and "Ambiguous snippet" in err

    # Empty/whitespace snippet
    _, err = apply_snippet_edits(current_html, replace_target_snippet="   ", replace_text="Job:")
    assert err and "cannot be empty or whitespace only" in err


def test_yaml_formatter_safe_injection_resistance():
    malicious = {
        "labels": ["feat", "admin: true # injected\nmalicious: [1, 2]"],
        "normal": "safe",
    }
    yaml_out = format_as_yaml(malicious)
    parsed = yaml.safe_load(yaml_out)
    assert parsed["labels"][1] == "admin: true # injected\nmalicious: [1, 2]"
    assert "admin" not in parsed
    assert "malicious" not in parsed


def test_yaml_formatter_anchor_alias_suppression():
    shared_list = ["apple", "banana"]
    data = {
        "basket1": shared_list,
        "basket2": shared_list,
    }
    yaml_out = format_as_yaml(data)
    assert "&id" not in yaml_out
    assert "*id" not in yaml_out
