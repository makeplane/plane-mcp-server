"""Pagination envelope helpers."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from plane_mcp.toolkit.paging import ENVELOPE_FIELDS, dump_results, envelope


class Item(BaseModel):
    id: str
    name: str = ""


class Page:
    """A response carrying a complete pagination envelope."""

    results = [{"id": "a"}]
    total_count = 9
    count = 1
    next_cursor = "NEXT"
    prev_cursor = "PREV"
    next_page_results = True
    prev_page_results = False


class Unpaginated:
    """What several Plane list responses actually look like: results, nothing else."""

    results = [{"id": "a"}]


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


def test_a_complete_page_keeps_every_envelope_field():
    enveloped = envelope(Page())
    assert enveloped["results"] == [{"id": "a"}]
    assert all(field in enveloped for field in ENVELOPE_FIELDS)
    assert enveloped["next_cursor"] == "NEXT"


def test_an_unpaginated_response_is_refused_rather_than_defaulted():
    """Six SDK response models carry no cursor fields.

    Defaulting them to None would hand back `next_cursor: null`, which reads as
    "that was the last page" -- a truncated answer presented as a complete one.
    The error names the fields and what to do instead.
    """
    with pytest.raises(TypeError) as caught:
        envelope(Unpaginated())

    message = str(caught.value)
    assert "Unpaginated" in message
    assert "next_cursor" in message
    assert "does not paginate" in message
