"""The operator WebSocket endpoint (/ws)."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.context import AppContext
from backend.app.ws.connection_manager import Connection
from backend.app.ws.handlers import handle_message

log = logging.getLogger("ws")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def origin_allowed(origin: str | None) -> bool:
    """Browsers always send Origin on WebSocket upgrades; only local pages may connect."""
    if not origin:
        return True  # non-browser clients (tests, curl) - the server only listens on 127.0.0.1
    return urlsplit(origin).hostname in {h.strip("[]") for h in LOCAL_HOSTS}


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        if not origin_allowed(websocket.headers.get("origin")):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        conn = Connection(websocket)
        ctx.ws.add(conn)
        try:
            await conn.send("state", ctx.recorder.get_state())
            while True:
                text = await websocket.receive_text()
                try:
                    raw = json.loads(text)
                except ValueError:
                    await conn.send("error", {"message": "Invalid JSON", "code": "protocol"})
                    continue
                await handle_message(ctx, conn, raw)
        except WebSocketDisconnect:
            pass
        finally:
            ctx.ws.remove(conn)

    return router
