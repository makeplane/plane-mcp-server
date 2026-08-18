"""Import-closure tests for the package's layering.

The direction of these edges is the part of the structure worth defending: offline reporting
must be usable without live-run code, fixtures without agent backends, and the recording
proxy without any of it. Every invariant here held when the tests were written, so a failure
means a new import changed the shape of the package rather than a pre-existing violation.

Each case runs in a fresh interpreter, because import closure is a property of a process.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

# (module to import, package names it must not drag in)
BOUNDARIES = [
    ("evals.report.load", ("runner", "drivers", "seed", "proxy")),
    ("evals.report.statistics", ("runner", "drivers", "seed")),
    ("evals.seed.build", ("drivers", "report", "runner")),
    ("evals.proxy", ("runner", "drivers", "seed", "tasks", "report")),
    ("evals.tasks", ("runner", "drivers", "report", "proxy")),
    # A pure token-counting helper once imported the live runner, so importing it loaded
    # every driver, seeder, task and report module in the tree.
    ("evals.listing", ("runner", "drivers", "seed", "tasks", "report")),
]


def loaded_subpackages(module: str) -> set[str]:
    """Return the ``evals.*`` subpackages present in sys.modules after importing ``module``."""
    code = (
        f"import {module}, sys\n"
        "print(' '.join(sorted({m.split('.')[1] for m in sys.modules "
        "if m.startswith('evals.') and m.count('.') >= 1})))"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(completed.stdout.split())


@pytest.mark.parametrize(("module", "forbidden"), BOUNDARIES, ids=[case[0] for case in BOUNDARIES])
def test_import_does_not_cross_layer(module: str, forbidden: tuple[str, ...]):
    leaked = loaded_subpackages(module) & set(forbidden)
    assert not leaked, f"importing {module} loaded {sorted(leaked)}, which it must not depend on"


def test_the_probe_can_actually_observe_a_violation():
    """A boundary test that cannot fail is worse than none: prove the probe sees imports."""
    assert "runner" in loaded_subpackages("evals.runner.live")


# The two driver surfaces are independent: the API driver owns its loop and speaks to a
# provider, while a CLI driver supervises a subprocess and reads a recording proxy. Neither
# needs anything the other has. Depth-1 names cannot express this — both live under
# ``drivers`` — so these cases match module prefixes instead.
#
# (module to import, module prefixes it must not drag in)
SURFACE_BOUNDARIES = [
    # Reading the registry must load no surface at all, or get_driver's per-vendor imports
    # are decoration: a flat re-export wall here once made every consumer load all five
    # agent CLIs, because Python runs a package's __init__ before any submodule.
    ("evals.drivers", ("evals.drivers.api.", "evals.drivers.cli.")),
    ("evals.drivers.api.base", ("evals.drivers.cli.",)),
    ("evals.drivers.api.driver", ("evals.drivers.cli.",)),
    ("evals.drivers.cli.base", ("evals.drivers.api.",)),
    ("evals.drivers.cli.claude", ("evals.drivers.api.",)),
]


def loaded_modules(module: str) -> set[str]:
    """Return the full names of the ``evals.*`` modules present after importing ``module``."""
    code = f"import {module}, sys\nprint(' '.join(sorted(m for m in sys.modules if m.startswith('evals'))))"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(completed.stdout.split())


@pytest.mark.parametrize(("module", "forbidden"), SURFACE_BOUNDARIES, ids=[case[0] for case in SURFACE_BOUNDARIES])
def test_import_does_not_cross_driver_surface(module: str, forbidden: tuple[str, ...]):
    leaked = sorted(name for name in loaded_modules(module) if name.startswith(forbidden))
    assert not leaked, f"importing {module} loaded {leaked}, which it must not depend on"


def test_the_surface_probe_can_actually_observe_a_violation():
    """Same guard as above, for the prefix probe: prove it sees a real intra-surface import."""
    assert "evals.drivers.cli.base" in loaded_modules("evals.drivers.cli.claude")


# Shared floor: modules under evals.core may import only each other (plus stdlib /
# third-party). A flat dump of helpers into core would silently reintroduce the
# invisible shared vocabulary this package exists to make visible.
#
# Membership is discovered, not listed. Naming a package after its position in the graph
# only holds if the position is checked, and a hand-maintained list makes that opt-in: a
# module dropped into core/ and left out of the list would import whatever it liked.
CORE_MODULES = tuple(
    f"evals.core.{path.stem}"
    for path in sorted((pathlib.Path(__file__).parents[2] / "evals" / "core").glob("*.py"))
    if path.stem != "__init__"
)


def test_core_is_not_empty():
    """Guard the discovery above: a bad glob would make every core case vanish silently."""
    assert len(CORE_MODULES) >= 11, CORE_MODULES


@pytest.mark.parametrize("module", CORE_MODULES, ids=list(CORE_MODULES))
def test_core_imports_only_core(module: str):
    """Importing any core module must load no evals module outside evals.core."""
    loaded = loaded_modules(module)
    leaked = sorted(
        name
        for name in loaded
        if name.startswith("evals.") and name != "evals.core" and not name.startswith("evals.core.")
    )
    assert not leaked, f"importing {module} loaded non-core evals modules: {leaked}"


def test_the_exception_module_is_the_floor_of_the_floor():
    """``errors`` may depend on nothing of ours at all, not even its core siblings.

    ``BOUNDARIES`` used to assert this, but it matches depth-1 package names, and once
    ``results`` moved under ``core`` its depth-1 name became ``core`` — so the entry
    forbidding ``results`` could no longer match anything. Asserted here at the granularity
    that survives the move.
    """
    siblings = sorted(name for name in loaded_modules("evals.core.errors") if name != "evals.core.errors")
    assert siblings == ["evals", "evals.core"], siblings
