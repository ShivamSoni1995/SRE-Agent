"""
auth.py — API key authentication.

Keys are stored as a comma-separated list in the API_KEYS env var.
Unprotected paths: /health, /metrics, /docs, /openapi.json, /

Set API_KEYS=key1,key2,key3 in your environment.
If API_KEYS is not set, auth is disabled (dev mode).
Pass the key as: Authorization: Bearer <key>  or  X-API-Key: <key>
"""
import os
import logging
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

UNPROTECTED = {"/health", "/metrics", "/docs", "/openapi.json", "/", "/redoc"}


def _load_keys() -> set[str]:
    raw = os.getenv("API_KEYS", "")
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        valid_keys = _load_keys()

        # Auth disabled in dev mode
        if not valid_keys:
            return await call_next(request)

        if request.url.path in UNPROTECTED:
            return await call_next(request)

        # Check Authorization: Bearer <key>
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header[7:].strip()
            if key in valid_keys:
                return await call_next(request)

        # Check X-API-Key: <key>
        api_key_header = request.headers.get("X-API-Key", "").strip()
        if api_key_header and api_key_header in valid_keys:
            return await call_next(request)

        logger.warning(f"Unauthorized request to {request.url.path}")
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
