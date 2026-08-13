"""Shared building blocks for Plane MCP tool surfaces.

These were previously underscore-prefixed modules inside `tools/v2/`, where the
leading underscore was not a privacy marker at all -- it was the filter the
module-discovery loop used to tell helpers apart from resources. That made the
package's most widely used API (`spec`, imported by all 28 resource modules)
look private, and made the naming of an ordinary helper file load-bearing.

Nothing here knows which catalogue is calling it, so a resource package can be
renamed without these having to move with it.

The four modules split by *when* they act:

    spec        declaration time -- describe a resource's actions
    runtime     call time -- validate parameters, shape SDK payloads
    paging      response time -- Plane's pagination envelope and PQL failures
    transforms  listing time -- reshape the advertised catalogue

Names are re-exported here so a resource module needs one import, not four. The
submodules stay importable for callers that want to be explicit.
"""

from __future__ import annotations

from plane_mcp.toolkit.paging import dump_results, envelope, pql_failure, workitem_page
from plane_mcp.toolkit.runtime import (
    as_params,
    coerce_list,
    ids_of,
    missing,
    needs,
    one_of,
    opt,
    page_params,
    require,
)
from plane_mcp.toolkit.spec import (
    Action,
    action_names,
    build_annotations,
    build_description,
)
from plane_mcp.toolkit.transforms import StripOutputSchemas

__all__ = [
    "Action",
    "StripOutputSchemas",
    "action_names",
    "as_params",
    "build_annotations",
    "build_description",
    "coerce_list",
    "dump_results",
    "envelope",
    "ids_of",
    "missing",
    "needs",
    "one_of",
    "opt",
    "page_params",
    "pql_failure",
    "require",
    "workitem_page",
]
