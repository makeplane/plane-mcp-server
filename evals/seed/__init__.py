"""Evaluation fixture creation and removal."""

from .build import check_workspace_fixture_collisions, collision_categories, seed
from .client import make_plane_client
from .customers import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    EVALUATION_CUSTOMER_PROPERTY_NAME,
    is_evaluation_customer_name,
    seed_customer,
)
from .customers import (
    EVALUATION_CUSTOMER_PROPERTY_NAME as DEBIAS_CUSTOMER_PROP_DISPLAY,
)
from .cycles import CYCLE_CURRENT, CYCLE_PAST, seed_cycles
from .gates import is_plan_gate, plan_gate_skips
from .intake import INTAKE_BILLING_TITLE, INTAKE_SPAM_TITLE, seed_intake
from .item_types import (
    BUG_TYPE_NAME,
    INCIDENT_TYPE_NAME,
    SEVERITY_PROPERTY_NAME,
    seed_item_type,
)
from .labels import LABEL_NAMES, seed_labels
from .modules import MODULE_COMPLETED_TITLES, MODULE_NAME, seed_module
from .plan import seed_plan
from .projects import (
    MAIN_PROJECT_BUG_TITLES,
    PLANE_PROJECT_IDENTIFIER_MAX_LENGTH,
    SECOND_PROJECT_BUG_TITLES,
    create_project_with_identifier_retry,
    enable_project_features,
    enable_workspace_features,
    is_identifier_collision,
    secrets,
    seed_second_project,
)
from .projects import (
    MAIN_PROJECT_BUG_TITLES as R6_MAIN_BUG_TITLES,
)
from .projects import (
    SECOND_PROJECT_BUG_TITLES as R6_SECOND_BUG_TITLES,
)
from .releases import (
    EVALUATION_RELEASE_TAG_VERSION,
    RELEASE_CHANGELOG_TEXT,
    RELEASE_NAME,
    seed_release,
)
from .releases import (
    EVALUATION_RELEASE_TAG_VERSION as DEBIAS_RELEASE_TAG_VERSION,
)
from .remove import CleanupFailure, TeardownError, teardown
from .work_items import (
    BLOCKING_REFERENCE_ADDRESS,
    BLOCKING_SOURCE_TITLE,
    BLOCKING_TARGET_TITLE,
    CHECKOUT_COMMENT_PHRASES,
    CHECKOUT_TIMEOUT_TITLE,
    DARK_MODE_TITLE,
    DUE_THIS_WEEK_TITLES,
    PAYMENT_WEBHOOK_TITLE,
    SIDEBAR_TITLE,
    UNFINISHED_CYCLE_TITLES,
    WORK_ITEM_FIXTURES,
    find_completed_state,
    list_states,
    require_activities,
    seed_work_items,
)
from .work_items import (
    BLOCKING_REFERENCE_ADDRESS as W7_URL,
)
from .work_items import (
    BLOCKING_SOURCE_TITLE as W7_SOURCE_TITLE,
)
from .work_items import (
    BLOCKING_TARGET_TITLE as W7_TARGET_TITLE,
)
from .work_items import (
    CHECKOUT_COMMENT_PHRASES as R5_COMMENT_PHRASES,
)
from .work_items import (
    CHECKOUT_TIMEOUT_TITLE as R5_TITLE,
)
from .work_items import (
    DARK_MODE_TITLE as W3_TITLE,
)
from .work_items import (
    DUE_THIS_WEEK_TITLES as R3_DUE_TITLES,
)
from .work_items import (
    PAYMENT_WEBHOOK_TITLE as R1_TITLE,
)
from .work_items import (
    PAYMENT_WEBHOOK_TITLE as W8_TITLE,
)
from .work_items import (
    SIDEBAR_TITLE as W2_TITLE,
)
from .work_items import (
    UNFINISHED_CYCLE_TITLES as W6_UNFINISHED_TITLES,
)
from .work_items import (
    WORK_ITEM_FIXTURES as ITEM_FIXTURES,
)
from .work_items import require_activities as _gate_activity_worker

__all__ = [
    "BUG_TYPE_NAME",
    "CUSTOMER_NAME",
    "CUSTOMER_REQUEST_NAME",
    "CleanupFailure",
    "CYCLE_CURRENT",
    "CYCLE_PAST",
    "DEBIAS_CUSTOMER_PROP_DISPLAY",
    "DEBIAS_RELEASE_TAG_VERSION",
    "DARK_MODE_TITLE",
    "DUE_THIS_WEEK_TITLES",
    "EVALUATION_CUSTOMER_PROPERTY_NAME",
    "EVALUATION_RELEASE_TAG_VERSION",
    "INTAKE_BILLING_TITLE",
    "INTAKE_SPAM_TITLE",
    "ITEM_FIXTURES",
    "INCIDENT_TYPE_NAME",
    "LABEL_NAMES",
    "MAIN_PROJECT_BUG_TITLES",
    "MODULE_COMPLETED_TITLES",
    "MODULE_NAME",
    "PAYMENT_WEBHOOK_TITLE",
    "PLANE_PROJECT_IDENTIFIER_MAX_LENGTH",
    "R1_TITLE",
    "R3_DUE_TITLES",
    "R5_COMMENT_PHRASES",
    "R5_TITLE",
    "R6_MAIN_BUG_TITLES",
    "R6_SECOND_BUG_TITLES",
    "RELEASE_CHANGELOG_TEXT",
    "RELEASE_NAME",
    "SECOND_PROJECT_BUG_TITLES",
    "SEVERITY_PROPERTY_NAME",
    "SIDEBAR_TITLE",
    "TeardownError",
    "UNFINISHED_CYCLE_TITLES",
    "W2_TITLE",
    "W3_TITLE",
    "W6_UNFINISHED_TITLES",
    "W7_SOURCE_TITLE",
    "W7_TARGET_TITLE",
    "W7_URL",
    "W8_TITLE",
    "WORK_ITEM_FIXTURES",
    "BLOCKING_REFERENCE_ADDRESS",
    "BLOCKING_SOURCE_TITLE",
    "BLOCKING_TARGET_TITLE",
    "CHECKOUT_COMMENT_PHRASES",
    "CHECKOUT_TIMEOUT_TITLE",
    "_gate_activity_worker",
    "check_workspace_fixture_collisions",
    "collision_categories",
    "create_project_with_identifier_retry",
    "enable_project_features",
    "enable_workspace_features",
    "find_completed_state",
    "is_identifier_collision",
    "is_evaluation_customer_name",
    "is_plan_gate",
    "plan_gate_skips",
    "list_states",
    "make_plane_client",
    "require_activities",
    "secrets",
    "seed",
    "seed_customer",
    "seed_cycles",
    "seed_intake",
    "seed_item_type",
    "seed_labels",
    "seed_module",
    "seed_plan",
    "seed_release",
    "seed_second_project",
    "seed_work_items",
    "teardown",
]
