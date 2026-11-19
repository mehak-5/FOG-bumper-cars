"""Tracks open operator WebSocket connections and fans messages out to them."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from backend.app.core.models import make_envelope

log = logging.getLogger("ws")


@dataclass(eq=False)
class Connection:
    ws: WebSocket
    wants_logs: bool = False

    async def send(self, msg_type: str, payload: Any = None) -> None:
        await self.ws.send_json(make_envelope(msg_type, payload))


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: set[Connection] = set()

    @property
    def count(self) -> int:
        return len(self._conns)

    def add(self, conn: Connection) -> None:
        self._conns.add(conn)

    def remove(self, conn: Connection) -> None:
        self._conns.discard(conn)

    async def broadcast(
        self, msg_type: str, payload: Any = None, *, logs_only: bool = False
    ) -> None:
        """Send to every (or every log-subscribed) client. Dead sockets are dropped silently."""
        targets = [c for c in self._conns if c.wants_logs or not logs_only]
        if not targets:
            return
        env = make_envelope(msg_type, payload)  # validated once, not per client

        async def one(conn: Connection) -> None:
            try:
                await conn.ws.send_json(env)
            except Exception:  # noqa: BLE001 - a broken client must not affect the others
                self._conns.discard(conn)
                with contextlib.suppress(Exception):
                    await conn.ws.close()

        await asyncio.gather(*(one(c) for c in targets))
