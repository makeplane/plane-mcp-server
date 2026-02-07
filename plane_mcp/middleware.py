"""HTTP logging middleware for structured Datadog logging."""

import logging
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("plane_mcp.http")


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs HTTP requests with structured fields for Datadog."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        # Extract request info before processing
        method = request.method
        path = request.url.path
        query = str(request.url.query) if request.url.query else None
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Process the request
        try:
            response = await call_next(request)
            status_code = response.status_code
            error = None
        except Exception as e:
            status_code = 500
            error = str(e)
            raise
        finally:
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log with structured fields
            log_data = {
                "http": {
                    "method": method,
                    "url": path,
                    "status_code": status_code,
                    "query_string": query,
                    "useragent": user_agent,
                },
                "network": {
                    "client": {"ip": client_ip},
                },
                "duration_ms": round(duration_ms, 2),
            }

            if error:
                log_data["error"] = {"message": error}

            # Choose log level based on status code
            if status_code >= 500:
                logger.error("HTTP %s %s %d", method, path, status_code, extra=log_data)
            elif status_code >= 400:
                logger.warning("HTTP %s %s %d", method, path, status_code, extra=log_data)
            else:
                logger.info("HTTP %s %s %d", method, path, status_code, extra=log_data)

        return response
