"""Offline tests for eval --surface plumbing and classification overlays."""

from __future__ import annotations

import pytest

from evals.run import KNOWN_SURFACES, classify_call, parse_args, stdio_server_env
from evals.tasks import TASKS_BY_ID, resolve_surface_tool_sets


@pytest.fixture(autouse=True)
def _eval_creds(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "test-key")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)


def test_known_surfaces():
    assert KNOWN_SURFACES == {"full", "v2", "v2-schema"}


def test_stdio_env_full_does_not_set_surface(monkeypatch):
    env = stdio_server_env(surface="full")
    assert "PLANE_MCP_SURFACE" not in env
    assert env["PLANE_API_KEY"] == "test-key"
    assert env["PLANE_WORKSPACE_SLUG"] == "test-ws"
    assert env["PLANE_BASE_URL"] == "https://api.plane.so"
    # Never inherits ambient secrets
    monkeypatch.setenv("SOME_SECRET", "x")
    env2 = stdio_server_env(surface="full")
    assert "SOME_SECRET" not in env2


def test_stdio_env_v2_sets_plane_mcp_surface():
    env = stdio_server_env(surface="v2")
    assert env["PLANE_MCP_SURFACE"] == "v2"
    assert env["PLANE_API_KEY"] == "test-key"


def test_stdio_env_v2_schema_sets_plane_mcp_surface():
    env = stdio_server_env(surface="v2-schema")
    assert env["PLANE_MCP_SURFACE"] == "v2-schema"


def test_parse_args_accepts_v2_and_full():
    a = parse_args(["--surface", "v2", "--dry-run"])
    assert a.surface == "v2"
    b = parse_args(["--surface", "full"])
    assert b.surface == "full"


def test_r1_v2_overlay_exact_find_work_items():
    r1 = TASKS_BY_ID["R1"]
    full = resolve_surface_tool_sets(r1, "full")
    assert full["classification"] == "exact"
    assert full["skip"] is None
    assert "list_work_items" in full["optimal_tools"]

    v2 = resolve_surface_tool_sets(r1, "v2")
    assert v2["classification"] == "exact"
    assert v2["skip"] is None
    assert v2["optimal_tools"] == {"find_work_items"}
    assert "list_work_items" not in v2["optimal_tools"]


def test_w1_v2_overlay():
    w1 = TASKS_BY_ID["W1"]
    v2 = resolve_surface_tool_sets(w1, "v2")
    assert v2["classification"] == "exact"
    assert "create_work_item" in v2["optimal_tools"]
    assert "get_workspace_context" in v2["optimal_tools"]
    assert v2["optimal_calls"] == 2


def test_s1_v2_unsupported_skip():
    s1 = TASKS_BY_ID["S1"]
    v2 = resolve_surface_tool_sets(s1, "v2")
    assert v2["skip"] is not None
    assert "schema" in v2["skip"].lower() or "property" in v2["skip"].lower() or "not on the" in v2["skip"]
    assert v2["classification"] == "exact"

    full = resolve_surface_tool_sets(s1, "full")
    assert full["skip"] is None
    assert "create_work_item_property" in full["optimal_tools"]


def test_s1_v2_schema_overlay():
    s1 = TASKS_BY_ID["S1"]
    out = resolve_surface_tool_sets(s1, "v2-schema")
    assert out["skip"] is None
    assert out["classification"] == "exact"
    assert out["optimal_tools"] == {"resolve_work_item_type", "create_work_item_property"}
    assert out["optimal_calls"] == 2


def test_unknown_surface_without_overlay_is_approximate():
    """A surface with no overlay falls back to flat sets + approximate."""
    r1 = TASKS_BY_ID["R1"]
    # Fabricate: use a surface name that has no overlay
    out = resolve_surface_tool_sets(r1, "experimental")
    assert out["classification"] == "approximate"
    assert out["skip"] is None
    assert out["optimal_tools"] == set(r1["optimal_tools"])


def test_classify_uses_resolved_sets():
    v2 = resolve_surface_tool_sets(TASKS_BY_ID["R1"], "v2")
    assert classify_call("find_work_items", v2["optimal_tools"], v2["alternate_tools"]) == "optimal"
    assert classify_call("list_work_items", v2["optimal_tools"], v2["alternate_tools"]) == "out_of_set"
    assert classify_call("get_work_item", v2["optimal_tools"], v2["alternate_tools"]) == "alternate"


def test_skip_path_no_network(monkeypatch):
    """Unsupported surface skip must not call seed/teardown/agent."""
    from evals import run as run_mod

    seeded = []
    torn = []

    monkeypatch.setattr(run_mod, "make_plane_client", lambda: (object(), "ws"))
    monkeypatch.setattr(
        run_mod,
        "seed",
        lambda *a, **k: seeded.append(1) or (_ for _ in ()).throw(AssertionError("seed should not run")),
    )
    monkeypatch.setattr(run_mod, "teardown", lambda *a, **k: torn.append(1))

    import asyncio
    import tempfile
    from pathlib import Path

    # Avoid importing anthropic for skip-only path: patch AsyncAnthropic too
    class _FakeAnthro:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: _FakeAnthro())

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.jsonl"
        rc = asyncio.run(
            run_mod.run_live(
                [TASKS_BY_ID["S1"]],
                model_alias="sonnet",
                reps=1,
                surface="v2",
                out_path=out,
            )
        )
        assert rc == 0
        assert seeded == []
        text = out.read_text(encoding="utf-8")
        assert "S1" in text
        assert "skipped" in text
        # First line may be meta header; pick the task row.
        rows = [__import__("json").loads(ln) for ln in text.strip().splitlines() if ln.strip()]
        row = next(r for r in rows if r.get("task_id") == "S1")
        assert row["surface"] == "v2"
        assert row["skipped"]
        assert row["classification"] == "exact"
