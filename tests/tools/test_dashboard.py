"""Dashboard behaviour the surface-wide suites cannot see: what reaches the v2 SDK, and how refusals read."""

from __future__ import annotations

from plane.api.v2 import FieldError, PlaneAPIError

WIDGETS = "v2.workspaces.dashboards.widgets"


def _call(registered, **arguments):
    return registered["dashboard"].fn(dashboard_id="d", **arguments)


def test_clear_sends_null_for_only_the_named_axis_fields(registered, spy):
    _call(registered, action="update_widget", widget_id="w", clear=["group_by"])

    sent = spy.recorder.only().kwargs["data"]
    assert sent.model_dump(exclude_unset=True) == {"group_by": None}


def test_a_field_cannot_be_both_set_and_cleared(registered, spy):
    result = _call(registered, action="update_widget", widget_id="w", group_by="PRIORITY", clear=["group_by"])

    assert result.startswith("Error:") and "group_by" in result
    assert not spy.recorder.calls


def test_clear_refuses_a_field_the_server_cannot_null(registered, spy):
    result = _call(registered, action="update_widget", widget_id="w", clear=["name"])

    assert result.startswith("Error: clear must be one of:")
    assert not spy.recorder.calls


def test_an_unset_field_is_not_sent(registered, spy):
    """An axis field marked set as None would go out as null and wipe it."""
    _call(registered, action="update_widget", widget_id="w", name="Renamed")

    assert spy.recorder.only().kwargs["data"].model_dump(exclude_unset=True) == {"name": "Renamed"}


def test_widget_pql_is_the_filter_its_chart_reads(registered, spy):
    _call(
        registered,
        action="create_widget",
        name="Open",
        chart_type="NUMBER",
        chart_model="BASIC",
        pql='state__group = "started"',
    )

    sent = spy.recorder.only().kwargs["data"]
    assert sent.pql_filters == {"stripped": 'state__group = "started"'}
    assert sent.last_used_filter == "pql_filters"


def test_row_ids_name_the_no_value_row_none(registered, spy):
    _call(registered, action="get_chart", widget_id="w", column_page=2, row_ids=["g-1", "none"])

    call = spy.recorder.only()
    assert call.method == f"{WIDGETS}.chart"
    assert call.kwargs["row_ids"] == ["g-1", None]
    assert call.kwargs["column_page"] == 2


def test_get_charts_refuses_more_than_the_server_charts(registered, spy):
    result = _call(registered, action="get_charts", widget_ids=[f"w-{i}" for i in range(51)])

    assert result.startswith("Error:")
    assert not spy.recorder.calls


def test_a_validation_refusal_names_its_fields(registered, spy):
    """`PlaneAPIError`'s message drops `errors`, which is what the caller has to fix."""
    spy.returns[f"{WIDGETS}.create"] = PlaneAPIError(
        status=400,
        type="invalid_request",
        code="invalid_request",
        detail="Widget is not valid.",
        errors=[FieldError(field="y_axis_metric", message="This chart type requires y_axis_metric.")],
    )

    result = _call(registered, action="create_widget", name="By priority", chart_type="BAR_CHART", chart_model="BASIC")

    assert result == "Error: Widget is not valid. (y_axis_metric: This chart type requires y_axis_metric.)"


def test_a_plan_refusal_is_answered_not_raised(registered, spy):
    spy.returns["v2.workspaces.dashboards.retrieve"] = PlaneAPIError(
        status=402, type="payment_required", code="plan_required", detail="Upgrade to use dashboards."
    )

    result = _call(registered, action="retrieve")

    assert result.startswith("Error:") and "plan" in result
