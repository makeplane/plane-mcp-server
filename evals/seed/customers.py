"""Customer fixtures for evaluation workspaces."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.customers import CreateCustomer, CreateCustomerRequest

CUSTOMER_NAME = "Acme Corp"
CUSTOMER_REQUEST_NAME = "SSO support"
EVALUATION_CUSTOMER_PROPERTY_NAME = "Eval Industry"


def seed_customer(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    customer = plane.customers.create(
        workspace_slug=workspace_slug,
        data=CreateCustomer(name=CUSTOMER_NAME),
    )
    context["customer"] = {"id": customer.id, "name": CUSTOMER_NAME}
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
