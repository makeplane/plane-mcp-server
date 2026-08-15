"""Public import compatibility after neutral fixture extraction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_seed_and_task_packages_import_in_either_order_with_legacy_reexports():
    root = Path(__file__).parents[2]
    assertions = """
from evals.errors import TaskSkipped as NeutralTaskSkipped
from evals.fixtures import CUSTOMER_NAME as NeutralCustomerName
from evals.seed import CUSTOMER_NAME, R1_TITLE
from evals.seed.customers import is_evaluation_customer_name
from evals.seed.releases import EVALUATION_RELEASE_TAG_VERSION
from evals.tasks.skip import TaskSkipped
assert CUSTOMER_NAME == NeutralCustomerName == 'Acme Corp'
assert R1_TITLE == 'Payment webhook drops retries'
assert EVALUATION_RELEASE_TAG_VERSION == 'eval-rc1'
assert is_evaluation_customer_name('Acme')
assert TaskSkipped is NeutralTaskSkipped
"""
    for imports in ("import evals.tasks\nimport evals.seed\n", "import evals.seed\nimport evals.tasks\n"):
        result = subprocess.run(
            [sys.executable, "-c", imports + assertions],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
