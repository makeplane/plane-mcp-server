"""Retired import path for :class:`evals.core.errors.TaskSkipped`.

Kept because it shipped and ``tests/evals/test_import_compat.py`` pins it. Nothing in the
package imports it: the canonical home is ``evals.core.errors``, which depends on nothing. Every
source module routed through here for a while, which left the neutral module unused and made
a compat shim look like the real one.
"""

from evals.core.errors import TaskSkipped as TaskSkipped

__all__ = ["TaskSkipped"]
