import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger("event_bus")

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]


class EventBus:
    """In-process asynchronous publish/subscribe event hub.
    
    Adheres strictly to the specification:
    - Events follow noun.verb pattern (e.g. 'ride.started', 'processing.queued').
    - Wildcard '*' subscribers receive all events (used by WebSocket forwarders).
    - Errors in one subscriber are isolated and never crash other subscribers or the publisher.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, name: str, handler: Handler) -> None:
        self._subs[name].append(handler)

    def unsubscribe(self, name: str, handler: Handler) -> None:
        if handler in self._subs[name]:
            self._subs[name].remove(handler)

    async def publish(self, name: str, payload: dict[str, Any] | None = None) -> None:
        data = payload or {}
        handlers = list(self._subs.get(name, [])) + list(self._subs.get("*", []))
        for h in handlers:
            # Shield each subscriber so one failure doesn't break others
            asyncio.create_task(self._safe_invoke(h, name, data))

    async def _safe_invoke(self, h: Handler, name: str, payload: dict[str, Any]) -> None:
        try:
            await h(name, payload)
        except Exception:
            logger.exception("Subscriber failed processing event '%s'", name)
