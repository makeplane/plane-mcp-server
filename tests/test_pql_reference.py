"""The PQL reference must not teach rules stricter than the API's.

A model treats the reference as the rules of the language. A cap stricter than
the API's makes it drop conditions the user asked for and return the wrong set
of work items; syntax the API does not parse makes it write queries that fail.

The reference states no size limit at all. The API enforces its own (50
conditions, nesting depth 16, in `ComplexFilterBackend`) and a query over them
comes back through `pql_failure` with the API's message and the reference, so
the model can correct it. A limit written here could only drift from the API's:
the 5-condition cap this reference once stated was Pi's translator limit, never
the API's.
"""

from __future__ import annotations

import re

import pytest

from plane_mcp.pql_reference import PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE

REFERENCES = pytest.mark.parametrize("text", [PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE], ids=["brief", "full"])


@REFERENCES
def test_the_reference_states_no_condition_cap(text):
    assert not re.search(r"(?i)\bmax\w*\b[^\n]*\bconditions\b", text)
    assert not re.search(r"(?i)\blimits\b", text)


@REFERENCES
def test_between_is_only_taught_in_the_form_the_api_parses(text):
    """The API grammar is `field BETWEEN a AND b`; `BETWEEN (a, b)` is a parse error."""
    assert "BETWEEN (" not in text
