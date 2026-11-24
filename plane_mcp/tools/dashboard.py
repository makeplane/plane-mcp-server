"""Dashboards: workspace-level pages of charts, and the widgets (charts) on them.

The first resource on Plane's v2 API (`client.v2`), which differs from v1 in two
ways that show here: lists page by `next_cursor`/`has_more` rather than v1's
envelope, and a refusal is a `PlaneAPIError` carrying per-field `errors`.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, Literal, TypeVar, get_args

from fastmcp import FastMCP
from plane.api.v2 import PlaneAPIError
from plane.models.v2.dashboards import (
    CopyDashboard,
    CreateDashboard,
    CreateWidget,
    Dashboard,
    UpdateDashboard,
    UpdateWidget,
    Widget,
    WidgetChart,
    WidgetChartModel,
    WidgetChartResult,
    WidgetDateGrouping,
    WidgetMetric,
    WidgetType,
    WidgetWriteChartType,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    cursor_page,
    missing,
    needs,
    one_of,
    opt,
    plan_gated,
)

NAME = "dashboard"
TITLE = "Dashboards"

# 0 is a real level, so it cannot use the 0 sentinel.
ACCESS = {"private": 0, "public": 1}

# The SDK's write models are the vocabulary; repeating it here would drift.
CHART_TYPES = get_args(WidgetWriteChartType)
CHART_MODELS = get_args(WidgetChartModel)
DATE_GROUPINGS = get_args(WidgetDateGrouping)
METRICS = get_args(WidgetMetric)

# The server charts at most this many widgets per get_charts call and answers 400 past it.
MAX_CHARTS = 50

_LAYOUT = ("width", "height", "x_axis_coord", "y_axis_coord")
_WIDGET_FIELDS = (
    "x_axis_property",
    "x_axis_date_grouping",
    "y_axis_metric",
    "group_by",
    "config",
    "pql",
    *_LAYOUT,
)
_DASHBOARD_FIELDS = ("access", "project_ids", "pql")
# The only widget fields the server stores null in, so the only ones `clear` can empty.
CLEARABLE = ("x_axis_property", "x_axis_date_grouping", "y_axis_metric", "group_by")

ACTIONS = (
    Action("list", (), ("search", "cursor", "per_page"), read=True),
    Action("retrieve", ("dashboard_id",), read=True),
    Action("create", ("name",), _DASHBOARD_FIELDS),
    Action(
        "update",
        ("dashboard_id",),
        ("name", *_DASHBOARD_FIELDS),
        note="project_ids replaces the list",
    ),
    Action("delete", ("dashboard_id",), destructive=True),
    Action("copy", ("dashboard_id",), ("name",)),
    Action("list_widget_types", (), note="with chart_models and plan availability", read=True),
    Action("list_widgets", ("dashboard_id",), ("cursor", "per_page"), read=True),
    Action("retrieve_widget", ("dashboard_id", "widget_id"), read=True),
    Action("create_widget", ("dashboard_id", "name", "chart_type", "chart_model"), _WIDGET_FIELDS),
    Action(
        "update_widget",
        ("dashboard_id", "widget_id"),
        ("name", "chart_type", "chart_model", *_WIDGET_FIELDS, "clear"),
        note="clear empties axis fields",
    ),
    Action("delete_widget", ("dashboard_id", "widget_id"), destructive=True),
    Action(
        "get_chart",
        ("dashboard_id", "widget_id"),
        ("page", "column_page", "row_ids"),
        note="column_page + row_ids on screen page TABLE_CHART columns",
        read=True,
    ),
    Action(
        "get_charts",
        ("dashboard_id",),
        ("widget_ids",),
        note=f"each widget's first page, at most {MAX_CHARTS}, each with its own status",
        read=True,
    ),
)

FOOTER = (
    f"access is one of: {', '.join(ACCESS)}. "
    "pql filters the work items charted. "
    "Axis charts need x_axis_property and y_axis_metric; it and group_by take e.g. PRIORITY, STATE_GROUPS "
    "or cp_<custom property id>. "
    'config is a JSON object of chart settings. row_ids: "none" is the no-value row.'
)

# A new resource: nothing was ever advertised under another name.
LEGACY: dict[str, str] = {}

F = TypeVar("F", bound=Callable[..., Any])


def _explained(fn: F) -> F:
    """Answer a 400 with the fields it names, which the exception's message drops.

    `PlaneAPIError`'s message is `HTTP 400 <code>: <detail>`; the per-field
    reasons -- a widget missing the setting its chart type needs, say -- are only
    in `errors`, and they are what a caller has to change.
    """

    @functools.wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except PlaneAPIError as exc:
            if exc.status != 400:
                raise
            reasons = "; ".join(f"{error.field}: {error.message}" for error in exc.errors or ())
            return f"Error: {exc.detail}" + (f" ({reasons})" if reasons else "")

    return guarded  # type: ignore[return-value]


def _json_object(raw: str) -> dict[str, Any] | str | None:
    """`config` parsed, None when unset, or an error naming what was wrong."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        return f"Error: config must be a JSON object; it is not valid JSON ({exc})."
    if not isinstance(parsed, dict):
        return f"Error: config must be a JSON object; got {type(parsed).__name__}."
    return parsed


def _vocabulary(**supplied: str) -> str | None:
    """The first enum-valued parameter outside its vocabulary, named with what it allows."""
    allowed = {
        "access": tuple(ACCESS),
        "chart_type": CHART_TYPES,
        "chart_model": CHART_MODELS,
        "x_axis_date_grouping": DATE_GROUPINGS,
        "y_axis_metric": METRICS,
    }
    for name, value in supplied.items():
        if error := one_of(name, value, allowed[name]):
            return error
    return None


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Dashboards of charts over a workspace's work items.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    @_explained
    @plan_gated("Dashboards")
    def dashboard(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "update",
            "delete",
            "copy",
            "list_widget_types",
            "list_widgets",
            "retrieve_widget",
            "create_widget",
            "update_widget",
            "delete_widget",
            "get_chart",
            "get_charts",
        ],
        dashboard_id: str = "",
        widget_id: str = "",
        name: str = "",
        access: str = "",
        project_ids: list[str] | None = None,
        pql: str = "",
        chart_type: str = "",
        chart_model: str = "",
        x_axis_property: str = "",
        x_axis_date_grouping: str = "",
        y_axis_metric: str = "",
        group_by: str = "",
        config: str = "",
        # Grid position and size: 0 is a real column or row, so these cannot use the 0 sentinel.
        width: int | None = None,
        height: int | None = None,
        x_axis_coord: int | None = None,
        y_axis_coord: int | None = None,
        clear: list[str] | None = None,
        widget_ids: list[str] | None = None,
        page: int = 0,
        column_page: int = 0,
        row_ids: list[str] | None = None,
        search: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> Dashboard | Widget | WidgetChart | list[WidgetType] | list[WidgetChartResult] | dict[str, Any] | str | None:
        if error := _vocabulary(
            access=access,
            chart_type=chart_type,
            chart_model=chart_model,
            x_axis_date_grouping=x_axis_date_grouping,
            y_axis_metric=y_axis_metric,
        ):
            return error
        settings = _json_object(config)
        if isinstance(settings, str):
            return settings
        for field in clear or ():
            if error := one_of("clear", field, CLEARABLE):
                return error

        client, workspace_slug = get_plane_client_context()
        dashboards = client.v2.workspaces.dashboards
        widgets = dashboards.widgets

        # Fields shared by create and update, of a dashboard and of a widget. An unset
        # value is left out, so an update changes only what the caller passed.
        board = {
            "access": ACCESS.get(access),
            "project_ids": project_ids,
            "pql_filters": {"stripped": pql} if pql else None,
        }
        chart = {
            "config": settings,
            "x_axis_property": opt(x_axis_property),
            "x_axis_date_grouping": opt(x_axis_date_grouping),
            "y_axis_metric": opt(y_axis_metric),
            "group_by": opt(group_by),
            "width": width,
            "height": height,
            "x_axis_coord": x_axis_coord,
            "y_axis_coord": y_axis_coord,
            # Charts read `filters` unless told otherwise, so pql has to say it is the one in use.
            **({"pql_filters": {"stripped": pql}, "last_used_filter": "pql_filters"} if pql else {}),
        }

        if action == "list":
            return cursor_page(
                dashboards.list(
                    workspace_slug,
                    paginate="cursor",
                    cursor=opt(cursor),
                    per_page=opt(per_page),
                    **({"search": search} if search else {}),
                )
            )

        if action == "create":
            if not name:
                return missing(action, "name")
            return dashboards.create(workspace_slug, CreateDashboard(name=name, **_set(board)))

        if action == "list_widget_types":
            return dashboards.widget_types(workspace_slug)

        if not dashboard_id:
            return missing(action, "dashboard_id")

        if action == "retrieve":
            return dashboards.retrieve(workspace_slug, dashboard_id)

        if action == "update":
            fields = _set({"name": opt(name), **board})
            if not fields:
                return missing(action, "name, access, project_ids or pql")
            return dashboards.update(workspace_slug, dashboard_id, UpdateDashboard(**fields))

        if action == "delete":
            dashboards.delete(workspace_slug, dashboard_id)
            return None

        if action == "copy":
            return dashboards.copy(workspace_slug, dashboard_id, CopyDashboard(name=opt(name)))

        if action == "list_widgets":
            return cursor_page(
                widgets.list(
                    workspace_slug, dashboard_id, paginate="cursor", cursor=opt(cursor), per_page=opt(per_page)
                )
            )

        if action == "get_charts":
            if widget_ids is not None and len(widget_ids) > MAX_CHARTS:
                return f"Error: get_charts takes at most {MAX_CHARTS} widget_ids; send them in batches."
            return widgets.charts(workspace_slug, dashboard_id, widget_ids=widget_ids or None)

        if action == "create_widget":
            if error := needs(action, name=name, chart_type=chart_type, chart_model=chart_model):
                return error
            return widgets.create(
                workspace_slug,
                dashboard_id,
                CreateWidget(name=name, chart_type=chart_type, chart_model=chart_model, **_set(chart)),
            )

        if not widget_id:
            return missing(action, "widget_id")

        if action == "retrieve_widget":
            return widgets.retrieve(workspace_slug, dashboard_id, widget_id)

        if action == "update_widget":
            fields = _set({"name": opt(name), "chart_type": opt(chart_type), "chart_model": opt(chart_model), **chart})
            if both := sorted(set(clear or ()) & set(fields)):
                return f"Error: {', '.join(both)} cannot be both set and cleared."
            # Set to None explicitly: the SDK sends a clearable field set to None as null.
            fields.update(dict.fromkeys(clear or ()))
            if not fields:
                return missing(action, "name, chart_type, chart_model, clear or a widget setting")
            return widgets.update(workspace_slug, dashboard_id, widget_id, UpdateWidget(**fields))

        if action == "delete_widget":
            widgets.delete(workspace_slug, dashboard_id, widget_id)
            return None

        return widgets.chart(
            workspace_slug,
            dashboard_id,
            widget_id,
            page=opt(page),
            column_page=opt(column_page),
            # The table's "no value" row has no id; a caller names it "none", the API wants null.
            row_ids=[None if row.lower() == "none" else row for row in row_ids] if row_ids else None,
        )


def _set(fields: dict[str, Any]) -> dict[str, Any]:
    """The fields that carry a value.

    Passing an unset field as `None` would mark it set on the SDK model: the
    widget update sends every set field, and an axis field set to `None` goes out
    as `null`, which clears it on the server.
    """
    return {key: value for key, value in fields.items() if value is not None}
