import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.metrics import http_requests_total, http_request_duration_seconds

# Endpoints to skip tracking (too noisy, not interesting)
_SKIP_PATHS = {"/metrics", "/health", "/docs", "/openapi.json", "/favicon.ico"}


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if path in _SKIP_PATHS:
            return await call_next(request)

        method = request.method
        start = time.perf_counter()

        response = await call_next(request)

        duration = time.perf_counter() - start
        status = str(response.status_code)

        # Normalise path params so /incidents/INC-ABC and /incidents/INC-XYZ
        # are tracked as the same label, not thousands of distinct series
        label_path = _normalise_path(path)

        http_requests_total.labels(
            method=method,
            endpoint=label_path,
            status_code=status,
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=label_path,
        ).observe(duration)

        return response


def _normalise_path(path: str) -> str:
    """Replace dynamic path segments with placeholders."""
    parts = path.split("/")
    normalised = []
    for part in parts:
        # INC-XXXXXX pattern
        if part.startswith("INC-"):
            normalised.append("{incident_id}")
        else:
            normalised.append(part)
    return "/".join(normalised)
