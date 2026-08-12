"""Pagination envelope helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from plane_mcp.toolkit.paging import dump_results


class Item(BaseModel):
    id: str
    name: str = ""


def test_sparse_fieldset_selects_only_requested_fields():
    assert dump_results([Item(id="a", name="n")], "id") == [{"id": "a"}]


def test_whitespace_and_empty_entries_in_fields_are_tolerated():
    assert dump_results([Item(id="a", name="n")], " id , ,name ") == [{"id": "a", "name": "n"}]


def test_no_fields_dumps_everything():
    assert dump_results([Item(id="a", name="n")], None) == [{"id": "a", "name": "n"}]


def test_none_and_empty_pages_are_empty_lists():
    assert dump_results(None, "id") == []
    assert dump_results([], None) == []


@pytest.mark.parametrize("fields", [None, "id"])
def test_plain_dicts_survive_both_paths(fields):
    """The `hasattr` guard protected only the no-fields branch, so this raised."""
    assert dump_results([{"id": "a", "name": "n"}], fields) == [{"id": "a", "name": "n"}]
