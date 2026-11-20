"""The operator WebSocket endpoint (/ws)."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.app.context import AppContext
from backend.app.security import origin_ok
from backend.app.ws.connection_manager import Connection
from backend.app.ws.handlers import handle_message

log = logging.getLogger("ws")


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        if not origin_ok(websocket.headers.get("origin"), ctx.cfg.server.port):
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
