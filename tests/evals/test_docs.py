"""The runbook must not re-acquire claims the code has stopped making.

Documentation drifts silently. These are the two claims that cost the most when stale: a
prerequisite that is no longer required turns people away from running the harness at all,
and a fingerprint description that omits the revision hides why results stopped comparing.
"""

from __future__ import annotations

import pytest

from evals import REPO_ROOT

README = (REPO_ROOT / "evals" / "README.md").read_text()
DESIGN = (REPO_ROOT / "evals" / "DESIGN.md").read_text()


@pytest.mark.parametrize(
    "case",
    ["optional-flag-server", "plan-gate-reason", "catalog-revision", "exit-zero-contract"],
)
def test_the_behaviours(case):
    if case == "optional-flag-server":
        assert "FEATURE_FLAG_SERVER_BASE_URL" in README, "the option should still be documented"
        index = README.index("FEATURE_FLAG_SERVER_BASE_URL")
        paragraph = README[max(0, index - 200) : index + 500]
        assert "not** required" in paragraph or "Optionally" in paragraph, (
            "the flag server stopped being a prerequisite when the seeders learned to skip; "
            "the runbook must not tell people otherwise"
        )
    elif case == "plan-gate-reason":
        assert "env:plan-gated:" in README
    elif case == "catalog-revision":
        assert "CATALOG_REVISION" in README
    else:
        assert "exit 0 does **not** mean the agent passed" in README
        assert "execution coverage" in README


def test_design_still_states_the_skip_contract_the_seeders_now_implement():
    assert "not rewritten as an agent task failure" in DESIGN
