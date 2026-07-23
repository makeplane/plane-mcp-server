"""Fall back to a full list endpoint when a Plane server doesn't expose the matching "-lite" route.

Self-hosted Plane Community Edition instances don't register the ``-lite``
endpoints added for Cloud performance (``projects-lite``, ``cycles-lite``,
``modules-lite``, ``members-lite``) -- calling them returns HTTP 404 even
though the regular endpoint and the underlying data are fine. When that
happens, retry against the full endpoint and reshape its response into the
lite envelope the tool advertises.

See: https://github.com/makeplane/plane-mcp-server/issues/126, /163, /169,
/170, /172.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from plane.errors.errors import HttpError
from pydantic import BaseModel

TLite = TypeVar("TLite", bound=BaseModel)


def lite_or_fallback(
    lite_call: Callable[[], TLite],
    full_call: Callable[[], Any],
    lite_item_cls: type[BaseModel],
    lite_response_cls: type[TLite],
) -> TLite:
    """Call `lite_call`; on HTTP 404 retry via `full_call` and reshape into the lite envelope.

    `full_call` may return either a paginated envelope (an object with a
    `results` list) or a bare list of items -- both shapes are normalized
    into `lite_response_cls`.
    """
    try:
        return lite_call()
    except HttpError as exc:
        if exc.status_code != 404:
            raise

    full = full_call()
    items = full if isinstance(full, list) else full.results
    results = [lite_item_cls.model_validate(item.model_dump()) for item in items]

    if isinstance(full, list):
        return lite_response_cls.model_validate(
            {
                "results": results,
                "total_count": len(results),
                "next_cursor": "",
                "prev_cursor": "",
                "next_page_results": False,
                "prev_page_results": False,
                "count": len(results),
                "total_pages": 1,
                "total_results": len(results),
            }
        )

    return lite_response_cls.model_validate({**full.model_dump(), "results": results})
