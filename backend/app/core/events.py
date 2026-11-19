"""Tiny async publish/subscribe bus connecting modules. Port of src/backend/shared/events.ts.

Modules talk to each other only through events. A failing handler is logged and never breaks
the other handlers or the publisher.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("bus")

Handler = Callable[[Any], Awaitable[None] | None]


class Events:
    # Commands
    RIDE_START = "ride.start"
    RIDE_END = "ride.end"
    APP_QUIT = "app.quit"
    # Recording
    RIDE_STARTED = "ride.started"
    RIDE_FINISHED = "ride.finished"
    STATE_CHANGED = "state.changed"
    CAMERA_LOST = "camera.lost"
    # Processing and reel
    PROCESSING_QUEUED = "processing.queued"
    PROCESSING_PROGRESS = "processing.progress"
    MOMENTS_READY = "moments.ready"
    REEL_READY = "reel.ready"
    PROCESSING_FAILED = "processing.failed"
    # Sharing and system
    SHARE_CREATED = "share.created"
    TUNNEL_UP = "tunnel.up"
    TUNNEL_DOWN = "tunnel.down"
    APP_NOTICE = "app.notice"
    LOG_ENTRY = "log.entry"

    @classmethod
    def all(cls) -> set[str]:
        return {v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str)}


class EventBus:
    def __init__(self, *, strict: bool = False) -> None:
        """strict=True rejects unknown topics (catches typos); off by default for tests."""
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._strict = strict

    def _check(self, topic: str) -> None:
        if self._strict and topic not in Events.all():
            raise ValueError(f"Unknown event topic: {topic!r}")

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register a handler; returns a function that removes it again."""
        self._check(topic)
        self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers[topic]:
                self._handlers[topic].remove(handler)

        return unsubscribe

    async def publish(self, topic: str, data: Any = None) -> None:
        """Resolve after every handler finished. Handler errors are logged, not raised."""
        self._check(topic)
        payload = {} if data is None else data
        handlers = list(self._handlers.get(topic, ()))
        await asyncio.gather(*(self._run(topic, h, payload) for h in handlers))

    @staticmethod
    async def _run(topic: str, handler: Handler, payload: Any) -> None:
        try:
            result = handler(payload)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Handler failed for %r", topic)
