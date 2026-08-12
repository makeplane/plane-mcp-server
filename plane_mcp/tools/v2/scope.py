"""Resources that Plane can govern at the workspace as well as the project.

Plane is moving configuration upward: a workspace can own the vocabulary that
projects use, so the same resource exists twice in the SDK under two namespaces,
with different id keyword names and, sometimes, a smaller set of operations.

Rather than let each module hand-roll `if project_id: ... else: ...`, a resource
declares its two scopes once and asks for the one that matches the call. Adding
governance to another resource is then a declaration, not a rewrite.

The idiom the model sees is uniform across every governed resource: **supply
project_id for the project's own, omit it for the workspace's.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Bound:
    """One scope of a governed resource, resolved for a single call."""

    namespace: Any
    workspace: bool
    id_kwarg: str
    scope_kwargs: dict[str, Any]
    supported: frozenset[str]

    def supports(self, action: str) -> bool:
        return action in self.supported

    def ids(self, identifier: str) -> dict[str, Any]:
        """The id keyword for this scope; the two scopes rarely agree on the name."""
        return {self.id_kwarg: identifier}

    def unsupported(self, action: str) -> str:
        return (
            f"Error: action '{action}' is not available at workspace scope for this resource. "
            "Pass project_id to work within a project."
        )


@dataclass(frozen=True, slots=True)
class Governed:
    """Where a resource lives at each scope, and what each scope can do."""

    project_namespace: str
    workspace_namespace: str
    project_id_kwarg: str
    workspace_id_kwarg: str
    project_actions: frozenset[str]
    workspace_actions: frozenset[str]

    def bind(self, client: Any, project_id: str) -> Bound:
        if project_id:
            return Bound(
                namespace=_reach(client, self.project_namespace),
                workspace=False,
                id_kwarg=self.project_id_kwarg,
                scope_kwargs={"project_id": project_id},
                supported=self.project_actions,
            )
        return Bound(
            namespace=_reach(client, self.workspace_namespace),
            workspace=True,
            id_kwarg=self.workspace_id_kwarg,
            scope_kwargs={},
            supported=self.workspace_actions,
        )


def _reach(client: Any, path: str) -> Any:
    for part in path.split("."):
        client = getattr(client, part)
    return client


FULL = frozenset({"list", "retrieve", "create", "update", "delete"})
NO_RETRIEVE = FULL - {"retrieve"}

WORK_ITEM_TYPE = Governed(
    project_namespace="work_item_types",
    workspace_namespace="workspace_work_item_types",
    project_id_kwarg="work_item_type_id",
    workspace_id_kwarg="type_id",
    project_actions=FULL,
    workspace_actions=FULL,
)

# Work item types are the only resource Plane governs at both scopes today.
#
# The SDK already carries `workspace_project_states` and `workspace_project_labels`
# with the same shape (list/create/update/delete, no single-record retrieve, hence
# NO_RETRIEVE above). Neither surface exposes them yet. When that is taken up, it
# is a declaration here plus a payload-model branch in the module -- no new tool,
# no new action, and the same idiom the model already knows: supply project_id for
# the project's own set, omit it for the workspace's.
