"""
Authentication middleware.

- /health and /slack/events and /sms/events are always open (webhooks need no auth).
- /ui/*  — HTTP Basic Auth (username ignored, password = API_KEY).
- everything else — X-API-Key header.

If API_KEY is not configured auth is skipped entirely (dev mode).
"""
import base64
import secrets

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

# Paths that must remain open regardless of auth configuration.
_OPEN_PATHS = {"/health", "/slack/events", "/sms/events"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # No key configured → open (dev mode)
        if not settings.api_key:
            return await call_next(request)

        if request.url.path in _OPEN_PATHS:
            return await call_next(request)

        if request.url.path.startswith("/ui"):
            return await self._check_basic_auth(request, call_next)

        return await self._check_api_key(request, call_next)

    async def _check_basic_auth(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
                _, _, password = decoded.partition(":")
                if secrets.compare_digest(password, settings.api_key or ""):
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            content="Unauthorized",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="AI Event Intelligence"'},
        )

    async def _check_api_key(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        key = request.headers.get(settings.api_key_header, "")
        if key and secrets.compare_digest(key, settings.api_key or ""):
            return await call_next(request)

        return JSONResponse(
            {"detail": f"Missing or invalid {settings.api_key_header} header"},
            status_code=401,
        )
