"""Browser-facing guards for the private operator app.

The operator app has no login: it is reachable only from this PC on 127.0.0.1. Two attacks can
still come from a web page the operator happens to visit, and both are blocked here:
- DNS rebinding (a hostile name resolving to 127.0.0.1): TrustedHostMiddleware in main.py.
- Cross-site request forgery / cross-site WebSocket hijacking: the Origin check below.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_origins(port: int) -> frozenset[str]:
    return frozenset(
        {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
    )


def origin_ok(origin: str | None, port: int) -> bool:
    """No Origin header means a non-browser client (curl, tests); browsers always send one on
    cross-site writes and WebSocket upgrades. An Origin that is present must be exactly us."""
    return origin is None or origin in allowed_origins(port)


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Rejects state-changing requests that a foreign web page triggered."""

    def __init__(self, app, port: int) -> None:  # noqa: ANN001
        super().__init__(app)
        self.port = port

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS and not origin_ok(
            request.headers.get("origin"), self.port
        ):
            return PlainTextResponse("Cross-origin request refused", status_code=403)
        return await call_next(request)
