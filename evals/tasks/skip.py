"""Retired import path for :class:`evals.errors.TaskSkipped`.

Kept because it shipped and ``tests/evals/test_import_compat.py`` pins it. Nothing in the
package imports it: the canonical home is ``evals.errors``, which depends on nothing. Every
source module routed through here for a while, which left the neutral module unused and made
a compat shim look like the real one.
"""

from evals.errors import TaskSkipped as TaskSkipped

__all__ = ["TaskSkipped"]
