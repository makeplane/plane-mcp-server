"""Import-closure tests for the package's layering.

The direction of these edges is the part of the structure worth defending: offline reporting
must be usable without live-run code, fixtures without agent backends, and the recording
proxy without any of it. Every invariant here held when the tests were written, so a failure
means a new import changed the shape of the package rather than a pre-existing violation.

Each case runs in a fresh interpreter, because import closure is a property of a process.
"""

from __future__ import annotations

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
    # The neutral exception module is the floor: it may depend on nothing of ours.
    ("evals.errors", ("runner", "drivers", "seed", "tasks", "report", "proxy", "results")),
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
