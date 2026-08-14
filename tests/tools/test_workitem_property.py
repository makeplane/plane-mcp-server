"""Defects where `workitem_property` answered plausibly instead of correctly.

Each test here corresponds to a call that succeeded, returned something a model
would believe, and was wrong. That failure mode is worse than an exception: an
error is a self-correction channel, a plausible wrong answer is not.
"""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError


def _set_value(registered, spy, value):
    registered["workitem_property"].fn(
        action="set_value",
        project_id="proj-1",
        workitem_id="wi-1",
        property_id="prop-1",
        value=value,
    )
    return spy.recorder.only().kwargs["data"].value


# --- value is typed by the property, and must not be guessed from its spelling ---


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("12345", "12345"),  # an employee id on a TEXT property
        ("007", "007"),  # leading zeros are meaningful
        ("1.5", "1.5"),  # a version string, not a decimal
        ("TRUE", "TRUE"),  # the literal word on a TEXT property
        ("hello", "hello"),
    ],
)
def test_a_string_value_reaches_the_sdk_as_a_string(sent, expected, registered, spy):
    """Text was being parsed into ints/floats/bools by looking at it."""
    assert _set_value(registered, spy, sent) == expected


@pytest.mark.parametrize("sent", [True, False, 5, 1.5, ["opt-1", "opt-2"]])
def test_a_typed_value_reaches_the_sdk_unchanged(sent, registered, spy):
    """BOOLEAN, DECIMAL and multi-value OPTION properties need real types."""
    assert _set_value(registered, spy, sent) == sent


def test_false_is_a_value_not_an_omission(registered, spy):
    """`False` is falsy; the required-parameter guard must not read it as unset."""
    result = registered["workitem_property"].fn(
        action="set_value", project_id="p", workitem_id="w", property_id="pr", value=False
    )
    assert not (isinstance(result, str) and result.startswith("Error:")), result
    assert spy.recorder.only().kwargs["data"].value is False


def test_set_value_still_requires_a_value(registered, spy):
    result = registered["workitem_property"].fn(action="set_value", project_id="p", workitem_id="w", property_id="pr")
    assert isinstance(result, str) and result.startswith("Error:") and "value" in result
    assert not spy.recorder.calls


# --- malformed options must fail loudly, not create a property with none ---


@pytest.mark.parametrize(
    "bad",
    ['[{"name": "A",]', '{"name": "A"}', '"just a string"', "[1, 2, 3]"],
)
def test_malformed_options_is_an_error_not_a_silent_drop(bad, registered, spy):
    """The property was being created with zero options and reported as success."""
    result = registered["workitem_property"].fn(
        action="create",
        project_id="proj-1",
        display_name="Tier",
        property_type="OPTION",
        options=bad,
    )
    assert isinstance(result, str) and result.startswith("Error:"), (
        f"malformed options {bad!r} was accepted; result was {result!r}"
    )
    assert "options" in result
    assert not spy.recorder.calls, "a property was created despite unusable options"


def test_well_formed_options_still_reach_the_sdk(registered, spy):
    registered["workitem_property"].fn(
        action="create",
        project_id="proj-1",
        display_name="Tier",
        property_type="OPTION",
        options='[{"name": "Gold"}, {"name": "Silver"}]',
    )
    sent = spy.recorder.only().kwargs["data"].options
    assert [o.name for o in sent] == ["Gold", "Silver"]


# --- a failed lookup must not be indistinguishable from an empty one ---


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_list_propagates_auth_and_server_errors(status, registered, spy):
    """An expired token returning `[]` reads to a model as 'no custom properties'."""
    spy.returns["workspace_work_item_properties.list"] = HttpError(f"boom {status}", status_code=status)

    with pytest.raises(HttpError):
        registered["workitem_property"].fn(action="list")


def test_list_still_falls_back_when_a_scope_is_genuinely_empty(registered, spy):
    """404 stays a fallback signal -- that is what the widening chain is for.

    The type-scoped list comes back empty (a property with no type association is
    invisible to it), the project-flat widening 404s, and the workspace lookup
    answers. That whole path must still end in `[]` rather than an exception.
    """
    spy.returns["work_item_properties.list_project"] = HttpError("nope", status_code=404)

    result = registered["workitem_property"].fn(action="list", project_id="proj-1", workitem_type_id="type-1")

    assert result == []
