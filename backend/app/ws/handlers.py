"""Dispatch of client -> server WebSocket messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.app.core.events import Events
from backend.app.core.models import ProtocolError, SessionNotes, parse_client_message
from backend.app.ws.connection_manager import Connection

if TYPE_CHECKING:
    from backend.app.context import AppContext

log = logging.getLogger("ws")


async def handle_message(ctx: AppContext, conn: Connection, raw: Any) -> None:
    try:
        msg_type, payload = parse_client_message(raw)
    except ProtocolError as err:
        await conn.send("error", {"message": str(err), "code": "protocol"})
        return

    if msg_type == "ping":
        await conn.send("pong")
    elif msg_type == "state.get":
        await conn.send("state", ctx.recorder.get_state())
    elif msg_type == "ride.start":
        ctx.recorder.update_trigger_state(_button_state("s", "ride start published"))
        await ctx.bus.publish(Events.RIDE_START, {"source": "button"})
    elif msg_type == "ride.end":
        ctx.recorder.update_trigger_state(_button_state("e", "trigger"))
        await ctx.bus.publish(Events.RIDE_END, {"reason": "trigger", "source": "button"})
    elif msg_type == "session.notes":
        assert isinstance(payload, SessionNotes)
        ctx.recorder.set_notes(payload.notes)
    elif msg_type == "logs.subscribe":
        conn.wants_logs = True
        handler = ctx.log_handler
        for entry in list(handler.recent) if handler else []:
            await conn.send("log", entry)
    elif msg_type == "logs.unsubscribe":
        conn.wants_logs = False


def _button_state(key: str, reason: str) -> dict[str, Any]:
    return {"source": "button", "last_key": key, "last_accepted": True, "last_reason": reason}
