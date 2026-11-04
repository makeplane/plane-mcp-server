"""Read-only GraphQL view over the Plane REST API.

Plane has no GraphQL endpoint. This module offers one inside the MCP server so a
caller can fetch a work item together with its state, labels, assignees, comments
and project in a single tool call instead of five.

Design:
- Schema-first (SDL below), executed with graphql-core against plain dicts.
- Resolvers call the Plane SDK. A request-scoped ``Context`` memoises lookups
  (projects, states, labels, members, work item details) so a list of 50 items
  costs one call per project, not one per item.
- ``Context.call`` counts SDK calls and refuses past ``MAX_CALLS``; queries are
  also depth-limited. Both protect the Plane instance from a runaway query.
- Queries only. There are no mutations: writes stay with the action tools.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from graphql import (
    GraphQLError,
    GraphQLResolveInfo,
    ValidationRule,
    build_schema,
    parse,
    specified_rules,
    validate,
)
from graphql import execute as graphql_execute
from graphql.language import FieldNode, FragmentDefinitionNode, OperationDefinitionNode
from plane.models.query_params import PaginatedQueryParams, WorkItemQueryParams

MAX_CALLS = 200
MAX_DEPTH = 7
MAX_PAGE = 100

SDL = """
type Query {
  "All projects of the workspace."
  projects: [Project!]!
  "One project by id or by identifier (e.g. PLANE)."
  project(id: ID, identifier: String): Project
  "One work item by identifier, e.g. PLANE-1."
  workItem(identifier: String!): WorkItem
  "Work items of a project (id or identifier, e.g. STEMMA; whole workspace if omitted), filtered by PQL."
  workItems(project: ID, pql: String, orderBy: String, first: Int = 50, after: String): WorkItemPage!
}

type WorkItemPage {
  totalCount: Int
  nextCursor: String
  hasNextPage: Boolean!
  results: [WorkItem!]!
}

type Project {
  id: ID!
  name: String!
  identifier: String!
  description: String
  network: Int
  states: [State!]!
  labels: [Label!]!
  members: [Member!]!
  cycles: [Cycle!]!
  modules: [Module!]!
  workItems(pql: String, orderBy: String, first: Int = 50, after: String): WorkItemPage!
}

type State {
  id: ID!
  name: String!
  group: String
  color: String
  isDefault: Boolean
}

type Label {
  id: ID!
  name: String!
  color: String
}

type Member {
  id: ID!
  displayName: String
  firstName: String
  lastName: String
  email: String
  role: Int
  isBot: Boolean
}

type Cycle {
  id: ID!
  name: String!
  startDate: String
  endDate: String
  totalIssues: Int
  completedIssues: Int
}

type Module {
  id: ID!
  name: String!
  status: String
  startDate: String
  targetDate: String
  totalIssues: Int
  completedIssues: Int
}

type WorkItem {
  id: ID!
  identifier: String!
  sequenceId: Int
  name: String!
  description: String
  priority: String
  startDate: String
  targetDate: String
  completedAt: String
  createdAt: String
  updatedAt: String
  project: Project!
  state: State
  labels: [Label!]!
  assignees: [Member!]!
  parent: WorkItem
  children(first: Int = 50): [WorkItem!]!
  comments: [Comment!]!
  links: [Link!]!
}

type Comment {
  id: ID!
  text: String
  createdAt: String
  author: Member
}

type Link {
  id: ID!
  title: String
  url: String!
}
"""

SCHEMA = build_schema(SDL)


@dataclass
class Context:
    """Request-scoped state: the SDK client, the workspace and the memo tables."""

    client: Any
    workspace: str
    calls: int = 0
    memo: dict[tuple, Any] = field(default_factory=dict)

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls > MAX_CALLS:
            raise GraphQLError(
                f"Query needs more than {MAX_CALLS} Plane API calls. Narrow it: filter with pql, "
                "lower `first`, or drop nested lists."
            )
        return fn(*args, **kwargs)

    def once(self, key: tuple, load: Callable[[], Any]) -> Any:
        if key not in self.memo:
            self.memo[key] = load()
        return self.memo[key]


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else dict(model)


def _collect(
    ctx: Context,
    method: Callable[..., Any],
    *args: Any,
    params_type: Callable[..., Any] = dict,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Every row of a paginated SDK list call (bounded by MAX_CALLS).

    Most list endpoints take a plain mapping for ``params``; a few (projects) insist on a
    model, passed as ``params_type``.
    """
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        params = params_type(per_page=MAX_PAGE, **({"cursor": cursor} if cursor else {}))
        response = ctx.call(method, *args, params=params, **kwargs)
        if isinstance(response, list):
            return [_dump(r) for r in response]
        rows += [_dump(r) for r in response.results]
        cursor = getattr(response, "next_cursor", None)
        if not getattr(response, "next_page_results", False) or not cursor:
            return rows


# --- memoised lookups ------------------------------------------------------


def projects(ctx: Context) -> list[dict[str, Any]]:
    return ctx.once(
        ("projects",), lambda: _collect(ctx, ctx.client.projects.list, ctx.workspace, params_type=PaginatedQueryParams)
    )


def project_by_id(ctx: Context, project_id: str) -> dict[str, Any]:
    for p in projects(ctx):
        if p["id"] == project_id:
            return p
    raise GraphQLError(f"Project {project_id} not found in workspace {ctx.workspace}.")


def resolve_project_id(ctx: Context, ref: str) -> str:
    """Accept a project id or its identifier (e.g. STEMMA, case-insensitive) and return the id."""
    wanted = ref.strip()
    rows = projects(ctx)
    for p in rows:
        if p["id"] == wanted:
            return p["id"]
    for p in rows:
        if p["identifier"].lower() == wanted.lower():
            return p["id"]
    raise GraphQLError(f"Project {ref} not found in workspace {ctx.workspace}.")


def states(ctx: Context, project_id: str) -> list[dict[str, Any]]:
    return ctx.once(("states", project_id), lambda: _collect(ctx, ctx.client.states.list, ctx.workspace, project_id))


def labels(ctx: Context, project_id: str) -> list[dict[str, Any]]:
    return ctx.once(("labels", project_id), lambda: _collect(ctx, ctx.client.labels.list, ctx.workspace, project_id))


def members(ctx: Context, project_id: str) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        return [_dump(m) for m in ctx.call(ctx.client.projects.get_members, ctx.workspace, project_id)]

    return ctx.once(("members", project_id), load)


def work_item_detail(ctx: Context, item: dict[str, Any]) -> dict[str, Any]:
    """The item with labels/assignees, which list endpoints do not always carry."""
    if "labels" in item and "assignees" in item:
        return item

    def load() -> dict[str, Any]:
        return _dump(ctx.call(ctx.client.work_items.retrieve, ctx.workspace, item["project"], item["id"]))

    return ctx.once(("detail", item["id"]), load)


def work_item_page(
    ctx: Context, project_id: str | None, pql: str | None, order_by: str | None, first: int, after: str | None
) -> dict[str, Any]:
    params = WorkItemQueryParams(
        pql=pql or None,
        order_by=order_by or None,
        per_page=max(1, min(first, MAX_PAGE)),
        cursor=after or None,
    )
    if project_id:
        response = ctx.call(ctx.client.work_items.list, ctx.workspace, project_id, params=params)
    else:
        response = ctx.call(ctx.client.work_items.list_workspace, ctx.workspace, params=params)
    return {
        "total_count": getattr(response, "total_count", None),
        "next_cursor": getattr(response, "next_cursor", None),
        "has_next_page": bool(getattr(response, "next_page_results", False)),
        "results": [_dump(r) for r in response.results],
    }


# --- resolvers -------------------------------------------------------------

Resolver = Callable[[Any, GraphQLResolveInfo], Any]
RESOLVERS: dict[str, Resolver] = {}


def resolver(path: str) -> Callable[[Resolver], Resolver]:
    def register(fn: Resolver) -> Resolver:
        RESOLVERS[path] = fn
        return fn

    return register


def _ctx(info: GraphQLResolveInfo) -> Context:
    return info.context


def _by_id(rows: list[dict[str, Any]], wanted: Any) -> dict[str, Any] | None:
    return next((r for r in rows if r["id"] == wanted), None)


@resolver("Query.projects")
def _(_src, info):
    return projects(_ctx(info))


@resolver("Query.project")
def _(_src, info, id=None, identifier=None):
    if not id and not identifier:
        raise GraphQLError("project needs `id` or `identifier`.")
    for p in projects(_ctx(info)):
        if p["id"] == id or (identifier and p["identifier"].lower() == identifier.lower()):
            return p
    return None


@resolver("Query.workItem")
def _(_src, info, identifier):
    ctx = _ctx(info)
    match = re.fullmatch(r"([A-Za-z0-9_]+)-(\d+)", identifier.strip())
    if not match:
        raise GraphQLError("identifier must look like PROJECT-123.")
    item = ctx.call(ctx.client.work_items.retrieve_by_identifier, ctx.workspace, match[1].upper(), int(match[2]))
    return _dump(item)


@resolver("Query.workItems")
def _(_src, info, project=None, pql=None, orderBy=None, first=50, after=None):
    ctx = _ctx(info)
    project_id = resolve_project_id(ctx, project) if project else None
    return work_item_page(ctx, project_id, pql, orderBy, first, after)


@resolver("Project.states")
def _(src, info):
    return states(_ctx(info), src["id"])


@resolver("Project.labels")
def _(src, info):
    return labels(_ctx(info), src["id"])


@resolver("Project.members")
def _(src, info):
    return members(_ctx(info), src["id"])


@resolver("Project.cycles")
def _(src, info):
    ctx = _ctx(info)
    return _collect(ctx, ctx.client.cycles.list, ctx.workspace, src["id"])


@resolver("Project.modules")
def _(src, info):
    ctx = _ctx(info)
    return _collect(ctx, ctx.client.modules.list, ctx.workspace, src["id"])


@resolver("Project.workItems")
def _(src, info, pql=None, orderBy=None, first=50, after=None):
    return work_item_page(_ctx(info), src["id"], pql, orderBy, first, after)


@resolver("State.isDefault")
def _(src, _info):
    return src.get("default")


@resolver("Member.displayName")
def _(src, _info):
    return src.get("display_name")


@resolver("Member.isBot")
def _(src, _info):
    return src.get("is_bot")


@resolver("WorkItem.identifier")
def _(src, info):
    project = project_by_id(_ctx(info), src["project"])
    return f"{project['identifier']}-{src['sequence_id']}"


@resolver("WorkItem.description")
def _(src, _info):
    return src.get("description_stripped")


@resolver("WorkItem.project")
def _(src, info):
    return project_by_id(_ctx(info), src["project"])


@resolver("WorkItem.state")
def _(src, info):
    state = src.get("state")
    if isinstance(state, dict):
        return state
    return _by_id(states(_ctx(info), src["project"]), state) if state else None


@resolver("WorkItem.labels")
def _(src, info):
    ctx = _ctx(info)
    wanted = work_item_detail(ctx, src).get("labels") or []
    known = labels(ctx, src["project"])
    return [
        row for label_id in wanted if (row := _by_id(known, label_id["id"] if isinstance(label_id, dict) else label_id))
    ]


@resolver("WorkItem.assignees")
def _(src, info):
    ctx = _ctx(info)
    wanted = work_item_detail(ctx, src).get("assignees") or []
    known = members(ctx, src["project"])
    return [row for who in wanted if (row := _by_id(known, who["id"] if isinstance(who, dict) else who))]


@resolver("WorkItem.parent")
def _(src, info):
    ctx = _ctx(info)
    parent = src.get("parent")
    if not parent:
        return None
    parent_id = parent["id"] if isinstance(parent, dict) else parent
    return ctx.once(
        ("item", parent_id),
        lambda: _dump(ctx.call(ctx.client.work_items.retrieve, ctx.workspace, src["project"], parent_id)),
    )


@resolver("WorkItem.children")
def _(src, info, first=50):
    page = work_item_page(_ctx(info), src["project"], f'childOf("{src["id"]}")', None, first, None)
    return page["results"]


@resolver("WorkItem.comments")
def _(src, info):
    ctx = _ctx(info)
    return _collect(ctx, ctx.client.work_items.comments.list, ctx.workspace, src["project"], src["id"])


@resolver("WorkItem.links")
def _(src, info):
    ctx = _ctx(info)
    return _collect(ctx, ctx.client.work_items.links.list, ctx.workspace, src["project"], src["id"])


@resolver("Comment.text")
def _(src, _info):
    return src.get("comment_stripped")


@resolver("Comment.author")
def _(src, info):
    actor = src.get("actor")
    if not actor:
        return None
    return _by_id(members(_ctx(info), src["project"]), actor["id"] if isinstance(actor, dict) else actor)


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL.sub("_", name).lower()


def field_resolver(source: Any, info: GraphQLResolveInfo, **args: Any) -> Any:
    """Explicit resolver if registered, else the dict key in snake_case."""
    fn = RESOLVERS.get(f"{info.parent_type.name}.{info.field_name}")
    if fn is not None:
        return fn(source, info, **args)
    return source.get(_snake(info.field_name)) if isinstance(source, dict) else None


class DepthLimit(ValidationRule):
    """Reject queries nested deeper than MAX_DEPTH, fragments included."""

    def enter_operation_definition(self, node: OperationDefinitionNode, *_args: Any) -> None:
        fragments = {
            d.name.value: d for d in self.context.document.definitions if isinstance(d, FragmentDefinitionNode)
        }

        def depth(selection_set, seen: frozenset[str]) -> int:
            deepest = 0
            for sel in selection_set.selections if selection_set else []:
                if isinstance(sel, FieldNode):
                    deepest = max(deepest, 1 + depth(sel.selection_set, seen))
                elif hasattr(sel, "name") and sel.kind == "fragment_spread" and sel.name.value not in seen:
                    frag = fragments.get(sel.name.value)
                    if frag:
                        deepest = max(deepest, depth(frag.selection_set, seen | {sel.name.value}))
                elif getattr(sel, "selection_set", None):
                    deepest = max(deepest, depth(sel.selection_set, seen))
            return deepest

        if depth(node.selection_set, frozenset()) > MAX_DEPTH:
            self.report_error(GraphQLError(f"Query is nested deeper than {MAX_DEPTH} levels.", node))


def execute(
    ctx: Context, query: str, variables: dict[str, Any] | None = None, operation_name: str | None = None
) -> dict[str, Any]:
    """Run a query; returns the GraphQL response shape ({data, errors})."""
    try:
        document = parse(query)
    except GraphQLError as exc:
        return {"data": None, "errors": [{"message": exc.message, "path": None}], "plane_api_calls": 0}
    problems = validate(SCHEMA, document, [*specified_rules, DepthLimit])
    if problems:
        return {"data": None, "errors": [{"message": e.message, "path": None} for e in problems], "plane_api_calls": 0}
    result = graphql_execute(
        SCHEMA,
        document,
        context_value=ctx,
        variable_values=variables or None,
        operation_name=operation_name or None,
        field_resolver=field_resolver,
    )
    out: dict[str, Any] = {"data": result.data}
    if result.errors:
        out["errors"] = [{"message": e.message, "path": list(e.path) if e.path else None} for e in result.errors]
    out["plane_api_calls"] = ctx.calls
    return out
