"""Isolated Public Share App (127.0.0.1:8001).

Exposed exclusively to Cloudflare Tunnel / ngrok.
Serves ONLY reel.mp4 and poster.jpg via SHA-256 tokens.
Never serves raw videos, session.json, or operator APIs.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from backend.app.config import AppConfig
from backend.app.services.share import ShareService

log = logging.getLogger("public.app")

HTML_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bumper Car Highlight Reel</title>
  <style>
    body {
      margin: 0;
      background: #0b0f19;
      color: #fff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
      box-sizing: border-box;
    }
    .container {
      max-width: 800px;
      width: 100%;
      text-align: center;
    }
    h1 {
      font-size: 1.5rem;
      margin-bottom: 1.25rem;
      font-weight: 600;
    }
    video {
      width: 100%;
      max-height: 70vh;
      border-radius: 8px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.6);
      background: #000;
    }
    .actions {
      margin-top: 1.5rem;
    }
    .btn {
      display: inline-block;
      padding: 12px 28px;
      background: #2563eb;
      color: #fff;
      text-decoration: none;
      border-radius: 6px;
      font-weight: 500;
      transition: background 0.2s;
    }
    .btn:hover {
      background: #1d4ed8;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>Your Highlight Reel</h1>
    <video controls playsinline preload="metadata" poster="/r/__TOKEN__/poster">
      <source src="/r/__TOKEN__/video" type="video/mp4">
      Your browser does not support HTML5 video playback.
    </video>
    <div class="actions">
      <a href="/r/__TOKEN__/download" class="btn" download>Download Video</a>
    </div>
  </div>
</body>
</html>
"""


def not_found_response() -> Response:
    """Return identical 404 for unknown, expired, or revoked tokens."""
    return PlainTextResponse("Not Found", status_code=404)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        max_requests: int = 60,
        window_seconds: float = 60.0,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_ip = request.headers.get("CF-Connecting-IP")
        if not client_ip:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()
        if not client_ip and request.client:
            client_ip = request.client.host
        client_ip = client_ip or "unknown"

        now = time.time()
        recent = [t for t in self.requests[client_ip] if now - t < self.window_seconds]
        if len(recent) >= self.max_requests:
            return PlainTextResponse("Too Many Requests", status_code=429)

        recent.append(now)
        self.requests[client_ip] = recent
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def create_public_app(
    cfg: AppConfig,
    share_service: ShareService,
    *,
    rate_limit_max: int = 60,
    rate_limit_window: float = 60.0,
) -> FastAPI:
    """Factory for the public port 8001 FastAPI app."""
    if cfg.server.public_port == cfg.server.port:
        raise ValueError(
            f"Public share app refuses to start: public_port ({cfg.server.public_port}) "
            f"must differ from operator port ({cfg.server.port})"
        )

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        title="Bumper Car Public Share",
    )

    app.add_middleware(
        RateLimitMiddleware,
        max_requests=rate_limit_max,
        window_seconds=rate_limit_window,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/r/{token}")
    async def get_player(token: str) -> Response:
        resolved = share_service.resolve_token(token)
        if resolved is None:
            return not_found_response()
        html = HTML_PLAYER_TEMPLATE.replace("__TOKEN__", token)
        return HTMLResponse(html)

    @app.get("/r/{token}/video")
    async def get_video(token: str) -> Response:
        resolved = share_service.resolve_token(token)
        if resolved is None:
            return not_found_response()
        reel_path = resolved["reel_path"]
        if not reel_path.is_file():
            return not_found_response()
        # Starlette's FileResponse automatically parses Range headers and sends 206 Partial Content
        return FileResponse(reel_path, media_type="video/mp4")

    @app.get("/r/{token}/poster")
    async def get_poster(token: str) -> Response:
        resolved = share_service.resolve_token(token)
        if resolved is None or not resolved.get("poster_path"):
            return not_found_response()
        poster_path = resolved["poster_path"]
        if not poster_path.is_file():
            return not_found_response()
        return FileResponse(poster_path, media_type="image/jpeg")

    @app.get("/r/{token}/download")
    async def download_video(token: str) -> Response:
        resolved = share_service.resolve_token(token)
        if resolved is None:
            return not_found_response()
        reel_path = resolved["reel_path"]
        if not reel_path.is_file():
            return not_found_response()
        return FileResponse(
            reel_path,
            media_type="video/mp4",
            filename="highlight_reel.mp4",
        )

    # Any unknown route returns identical 404
    @app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
    async def catch_all(path_name: str) -> Response:
        return not_found_response()

    return app
