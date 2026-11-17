"""Offline eval tests for answers."""

from __future__ import annotations


def test_reports_contract_int_unit():
    """Direct unit cases for the contract helper."""
    from evals.tasks.answers import reports_contract_int

    assert reports_contract_int("count: 3", 3) is True
    assert reports_contract_int("count: 2", 3) is False
    assert reports_contract_int("-3", 3) is False
    assert reports_contract_int("count: -3", 3) is False
    assert reports_contract_int("0", 0) is True
    assert reports_contract_int("Some prose only", 0) is False
    assert reports_contract_int("preamble\ncount: 0\n", 0) is True
    # Last contract line wins
    assert reports_contract_int("count: 9\ncount: 3", 3) is True
    assert reports_contract_int("count: 9\ncount: 3", 9) is False


def test_exact_line_contract_helpers_unit():
    from evals.tasks.answers import contract_values, reports_contract_value, reports_contract_values

    text = "prose mentions state Done\nSTATE: In Progress\nitem: B\nitem: A"
    assert contract_values(text, "state") == ["In Progress"]
    assert reports_contract_value(text, "state", "In Progress") is True
    assert reports_contract_value("- state: In Progress", "state", "In Progress") is False
    assert reports_contract_values(text, "item", ["A", "B"]) is True
    assert reports_contract_values("item: A\nitem: A", "item", ["A"]) is False
