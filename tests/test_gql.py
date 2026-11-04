"""GraphQL layer: nested reads cost few Plane calls and are bounded."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plane_mcp import gql


class Row(SimpleNamespace):
    def model_dump(self, mode="python"):
        return dict(vars(self))


def page(*rows, nxt=None):
    return SimpleNamespace(results=list(rows), next_cursor=nxt, next_page_results=bool(nxt), total_count=len(rows))


PROJECT = Row(id="p1", name="Plane", identifier="PLANE", description="d", network=2)
STATE = Row(id="s1", name="Todo", group="unstarted", color="#fff", default=True)
LABEL = Row(id="l1", name="mcp", color="#000")
MEMBER = Row(id="u1", display_name="Daniel", first_name="D", last_name="S", email="d@x", role=20, is_bot=False)


def item(n: int, **extra):
    base = dict(id=f"i{n}", project="p1", sequence_id=n, name=f"Item {n}", state="s1", parent=None, priority="high")
    return Row(**{**base, **extra})


class Fake:
    """Counts calls per endpoint so tests can assert on N+1 behaviour."""

    def __init__(self, items):
        self.items = items
        self.counts: dict[str, int] = {}
        self.list_params = []

        def counted(name, value):
            def call(*a, **k):
                self.counts[name] = self.counts.get(name, 0) + 1
                return value(*a, **k) if callable(value) else value

            return call

        def list_projects(ws, params=None):
            # The real SDK calls params.model_dump(): a plain dict fails there (found live).
            assert hasattr(params, "model_dump"), "projects.list needs a params model"
            return page(PROJECT)

        self.projects = SimpleNamespace(
            list=counted("projects", list_projects), get_members=counted("members", [MEMBER])
        )
        self.states = SimpleNamespace(list=counted("states", page(STATE)))
        self.labels = SimpleNamespace(list=counted("labels", page(LABEL)))
        detail = lambda ws, project, item_id: Row(  # noqa: E731
            **{**vars(next(i for i in self.items if i.id == item_id)), "labels": ["l1"], "assignees": ["u1"]}
        )

        def list_items(ws, project, params=None):
            self.list_params.append(params)
            return page(*self.items)

        self.work_items = SimpleNamespace(
            list=counted("list", list_items),
            list_workspace=counted("list_ws", lambda ws, params=None: page(*self.items)),
            retrieve=counted("retrieve", detail),
            retrieve_by_identifier=counted("by_identifier", lambda ws, proj, n: detail(ws, "p1", f"i{n}")),
            comments=SimpleNamespace(
                list=counted(
                    "comments",
                    lambda ws, project, item_id, params=None: page(
                        Row(id="c1", comment_stripped="hi", created_at="t", actor="u1", project="p1")
                    ),
                )
            ),
            links=SimpleNamespace(list=counted("links", page(Row(id="k1", title="t", url="http://x")))),
        )


def run(fake, query, **kw):
    return gql.execute(gql.Context(client=fake, workspace="tf"), query, **kw)


def test_one_item_with_everything_nested():
    fake = Fake([item(1)])
    out = run(
        fake,
        '{ workItem(identifier: "plane-1") { identifier name state { name isDefault } labels { name } '
        "assignees { displayName } comments { text author { displayName } } links { url } project { name } } }",
    )
    assert "errors" not in out, out
    assert out["data"]["workItem"] == {
        "identifier": "PLANE-1",
        "name": "Item 1",
        "state": {"name": "Todo", "isDefault": True},
        "labels": [{"name": "mcp"}],
        "assignees": [{"displayName": "Daniel"}],
        "comments": [{"text": "hi", "author": {"displayName": "Daniel"}}],
        "links": [{"url": "http://x"}],
        "project": {"name": "Plane"},
    }


def test_lists_do_not_cost_one_call_per_item():
    fake = Fake([item(n) for n in range(1, 21)])
    out = run(fake, '{ workItems(project: "p1", first: 20) { totalCount results { identifier state { name } } } }')
    assert len(out["data"]["workItems"]["results"]) == 20
    assert fake.counts["states"] == 1
    assert fake.counts["projects"] == 1


def test_labels_fetch_detail_once_per_item_and_share_the_label_table():
    fake = Fake([item(n) for n in range(1, 4)])
    run(fake, '{ workItems(project: "p1") { results { labels { name } assignees { displayName } } } }')
    assert fake.counts["retrieve"] == 3
    assert fake.counts["labels"] == 1
    assert fake.counts["members"] == 1


def test_children_use_a_child_of_filter():
    fake = Fake([item(1)])
    run(fake, '{ workItem(identifier: "PLANE-1") { children { name } } }')
    assert fake.list_params[-1].pql == 'childOf("i1")'


def test_workspace_wide_listing_when_project_omitted():
    fake = Fake([item(1)])
    out = run(fake, "{ workItems { results { identifier } } }")
    assert out["data"]["workItems"]["results"] == [{"identifier": "PLANE-1"}]
    assert fake.counts["list_ws"] == 1


@pytest.mark.parametrize("ref", ["p1", "PLANE", "plane"])
def test_work_items_project_accepts_id_or_identifier(ref):
    fake = Fake([item(1)])
    out = run(fake, f'{{ workItems(project: "{ref}") {{ results {{ identifier }} }} }}')
    assert out["data"]["workItems"]["results"] == [{"identifier": "PLANE-1"}]
    assert fake.counts["list"] == 1
    assert fake.counts.get("list_ws", 0) == 0
    assert fake.counts["projects"] == 1


def test_work_items_unknown_project_is_a_graphql_error():
    fake = Fake([item(1)])
    out = run(fake, '{ workItems(project: "NOPE") { results { identifier } } }')
    assert "not found" in out["errors"][0]["message"]
    assert fake.counts.get("list", 0) == 0


def test_bad_identifier_is_a_graphql_error():
    out = run(Fake([]), '{ workItem(identifier: "nope") { name } }')
    assert "PROJECT-123" in out["errors"][0]["message"]


def test_mutations_do_not_exist():
    out = run(Fake([]), 'mutation { deleteWorkItem(id: "i1") }')
    assert out["data"] is None
    assert out["errors"]


def test_too_deep_queries_are_refused():
    deep = '{ workItem(identifier: "PLANE-1") ' + "{ parent " * 8 + "{ name }" + " }" * 8 + " }"
    out = run(Fake([item(1)]), deep)
    assert "nested deeper" in out["errors"][0]["message"]


def test_runaway_queries_hit_the_call_budget(monkeypatch):
    monkeypatch.setattr(gql, "MAX_CALLS", 5)
    fake = Fake([item(n) for n in range(1, 11)])
    out = run(fake, '{ workItems(project: "p1") { results { comments { text } } } }')
    assert any("more than 5 Plane API calls" in e["message"] for e in out["errors"])


def test_variables_and_operation_name():
    fake = Fake([item(1)])
    out = run(
        fake,
        "query Q($id: String!) { workItem(identifier: $id) { name } }",
        variables={"id": "PLANE-1"},
        operation_name="Q",
    )
    assert out["data"]["workItem"] == {"name": "Item 1"}


@pytest.mark.parametrize("first", [0, 1000])
def test_page_size_is_clamped(first):
    fake = Fake([item(1)])
    run(fake, f'{{ workItems(project: "p1", first: {first}) {{ results {{ name }} }} }}')
    assert 1 <= fake.list_params[-1].per_page <= gql.MAX_PAGE
