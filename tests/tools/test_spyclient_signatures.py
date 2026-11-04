"""The spy must bind against an SDK method whose annotations will not evaluate.

plane-sdk declares twelve methods as ``def list(self, ...) -> list[...]``. Under
Python 3.14's lazy annotations (PEP 649) the class namespace is in scope when the
annotation is evaluated, so ``list`` resolves to the method rather than the builtin
and subscripting it raises TypeError. That took out 17 tests across four files --
none of which are about annotations -- because ``inspect.signature`` does the
evaluating.

Nothing fails at runtime: the server advertises all 28 tools and a full eval battery
runs clean. The breakage is confined to test-time introspection.
"""

import inspect

from tests.tools._spyclient import UNEVALUATED_ANNOTATIONS, _signature_of


class ShadowingResource:
    """The exact shape plane-sdk uses, reproduced so this test needs no SDK version."""

    def list(self, workspace_slug: str) -> list[int]:
        return []


def test_the_shadowing_pattern_still_yields_a_bindable_signature():
    signature = _signature_of("shadow.list", ShadowingResource.list)
    assert list(signature.parameters) == ["self", "workspace_slug"]
    bound = signature.bind(ShadowingResource(), workspace_slug="acme")
    assert bound.arguments["workspace_slug"] == "acme"


def test_a_degraded_signature_is_recorded_rather_than_silently_accepted():
    """A type-check that quietly checks nothing must not look like a passing one."""
    try:
        inspect.signature(ShadowingResource.list)
    except TypeError:
        # This interpreter evaluates annotations eagerly here, so the fallback ran.
        _signature_of("shadow.list", ShadowingResource.list)
        assert "shadow.list" in UNEVALUATED_ANNOTATIONS
    else:
        # Pre-3.14: no fallback needed, so nothing should be recorded for it.
        _signature_of("shadow.list", ShadowingResource.list)
        assert "shadow.list" not in UNEVALUATED_ANNOTATIONS


def test_an_ordinary_signature_is_untouched():
    def plain(a: int, b: str = "x") -> bool:
        return True

    signature = _signature_of("plain", plain)
    assert list(signature.parameters) == ["a", "b"]
    assert signature.parameters["a"].annotation is int
    assert "plain" not in UNEVALUATED_ANNOTATIONS
