"""Unit tests for plane_mcp.lite_fallback."""

import pytest
from plane.errors.errors import HttpError
from pydantic import BaseModel

from plane_mcp.lite_fallback import lite_or_fallback


class _LiteItem(BaseModel):
    id: str
    name: str


class _LiteResponse(BaseModel):
    results: list[_LiteItem]
    total_count: int
    next_cursor: str
    prev_cursor: str
    next_page_results: bool
    prev_page_results: bool
    count: int
    total_pages: int
    total_results: int


class _FullItem(BaseModel):
    id: str
    name: str
    extra_field: str


class _FullResponse(BaseModel):
    results: list[_FullItem]
    total_count: int
    next_cursor: str
    prev_cursor: str
    next_page_results: bool
    prev_page_results: bool
    count: int
    total_pages: int
    total_results: int


def test_returns_lite_result_when_lite_call_succeeds():
    lite_response = _LiteResponse(
        results=[_LiteItem(id="1", name="a")],
        total_count=1,
        next_cursor="",
        prev_cursor="",
        next_page_results=False,
        prev_page_results=False,
        count=1,
        total_pages=1,
        total_results=1,
    )
    full_call_invoked = False

    def full_call():
        nonlocal full_call_invoked
        full_call_invoked = True
        raise AssertionError("full_call should not be invoked when lite_call succeeds")

    result = lite_or_fallback(lambda: lite_response, full_call, _LiteItem, _LiteResponse)

    assert result is lite_response
    assert not full_call_invoked


def test_falls_back_to_paginated_full_response_on_404():
    full_response = _FullResponse(
        results=[_FullItem(id="1", name="a", extra_field="x")],
        total_count=1,
        next_cursor="100:0:0",
        prev_cursor="",
        next_page_results=False,
        prev_page_results=False,
        count=1,
        total_pages=1,
        total_results=1,
    )

    def lite_call():
        raise HttpError("Not Found", status_code=404, response={"error": "Page not found."})

    result = lite_or_fallback(lite_call, lambda: full_response, _LiteItem, _LiteResponse)

    assert isinstance(result, _LiteResponse)
    assert result.results == [_LiteItem(id="1", name="a")]
    assert result.next_cursor == "100:0:0"
    assert result.total_count == 1


def test_falls_back_to_bare_list_full_response_on_404():
    full_items = [_FullItem(id="1", name="a", extra_field="x"), _FullItem(id="2", name="b", extra_field="y")]

    def lite_call():
        raise HttpError("Not Found", status_code=404, response={"error": "Page not found."})

    result = lite_or_fallback(lite_call, lambda: full_items, _LiteItem, _LiteResponse)

    assert isinstance(result, _LiteResponse)
    assert result.results == [_LiteItem(id="1", name="a"), _LiteItem(id="2", name="b")]
    assert result.total_count == 2
    assert result.next_page_results is False


def test_reraises_non_404_http_errors():
    def lite_call():
        raise HttpError("Server Error", status_code=500, response={"error": "boom"})

    def full_call():
        raise AssertionError("full_call should not be invoked on non-404 errors")

    with pytest.raises(HttpError) as exc_info:
        lite_or_fallback(lite_call, full_call, _LiteItem, _LiteResponse)

    assert exc_info.value.status_code == 500


def test_propagates_error_from_full_call_after_404():
    def lite_call():
        raise HttpError("Not Found", status_code=404, response={"error": "Page not found."})

    def full_call():
        raise HttpError("Server Error", status_code=500, response={"error": "boom"})

    with pytest.raises(HttpError) as exc_info:
        lite_or_fallback(lite_call, full_call, _LiteItem, _LiteResponse)

    assert exc_info.value.status_code == 500


def test_falls_back_to_empty_bare_list_on_404():
    def lite_call():
        raise HttpError("Not Found", status_code=404, response={"error": "Page not found."})

    result = lite_or_fallback(lite_call, lambda: [], _LiteItem, _LiteResponse)

    assert isinstance(result, _LiteResponse)
    assert result.results == []
    assert result.total_count == 0
