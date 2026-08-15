"""Customer fixtures for evaluation workspaces."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.customers import CreateCustomer, CreateCustomerRequest

from evals.fixtures import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    EVALUATION_CUSTOMER_PROPERTY_NAME,
    is_evaluation_customer_name,
)

from .identities import record_seeded_entity
from .projects import plan_gate_skips

__all__ = [
    "CUSTOMER_NAME",
    "CUSTOMER_REQUEST_NAME",
    "EVALUATION_CUSTOMER_PROPERTY_NAME",
    "is_evaluation_customer_name",
    "seed_customer",
]


def seed_customer(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Seed the L4 customer fixture, skipping the task when the plan excludes customers."""
    with plan_gate_skips("customers"):
        customer = plane.customers.create(
            workspace_slug=workspace_slug,
            data=CreateCustomer(name=CUSTOMER_NAME),
        )
        context["customer"] = {"id": customer.id, "name": CUSTOMER_NAME}
        record_seeded_entity(context, "customer", customer.id)
        context["workspace_objects"].append({"kind": "customer", "id": customer.id})
        request = plane.customers.requests.create(
            workspace_slug=workspace_slug,
            customer_id=customer.id,
            data=CreateCustomerRequest(name=CUSTOMER_REQUEST_NAME),
        )
    context["customer_request"] = {
        "id": request.id,
        "name": CUSTOMER_REQUEST_NAME,
        "customer_id": customer.id,
    }
    record_seeded_entity(context, "customer_request", request.id)
