"""Agent drivers: run one task against a tool surface and return an ``AgentRun``.

The ``api`` driver owns a provider-neutral loop; CLI drivers spawn locally installed
agent CLIs on the user's own subscription. Probed CLI details live with each vendor.

Only the registry lives here, and each driver is imported inside the branch that returns
it. This file used to re-export forty names, most of them vendor internals reached only by
tests — and because Python runs a package's ``__init__`` before any submodule, that wall
made *every* consumer load all five agent CLIs. Importing the API backend, which shares
nothing with them, pulled in the whole CLI tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evals.drivers.api.driver import ApiDriver
    from evals.drivers.cli.base import CliDriver

KNOWN_DRIVERS = frozenset({"api", "claude-cli", "codex-cli", "antigravity-cli", "opencode-cli"})


def get_driver(name: str, **kwargs: Any) -> ApiDriver | CliDriver:
    """Return a driver instance, loading only the surface it names."""
    key = (name or "api").strip().lower()
    if key == "api":
        from evals.drivers.api.driver import ApiDriver

        return ApiDriver(**kwargs)
    if key == "claude-cli":
        from evals.drivers.cli.claude import ClaudeCliDriver

        return ClaudeCliDriver(**kwargs)
    if key == "codex-cli":
        from evals.drivers.cli.codex import CodexCliDriver

        return CodexCliDriver(**kwargs)
    if key == "antigravity-cli":
        from evals.drivers.cli.antigravity import AntigravityCliDriver

        return AntigravityCliDriver(**kwargs)
    if key == "opencode-cli":
        from evals.drivers.cli.opencode import OpencodeCliDriver

        return OpencodeCliDriver(**kwargs)
    raise ValueError(f"unknown driver {name!r}; expected one of {sorted(KNOWN_DRIVERS)}")


__all__ = ["KNOWN_DRIVERS", "get_driver"]
