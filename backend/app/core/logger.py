"""Logging setup: rotating log files plus a handler that mirrors records onto the EventBus."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from backend.app.core.events import EventBus, Events

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_file_logging(logs_dir: Path, level: int = logging.INFO) -> RotatingFileHandler:
    """Rotating file log (5 x 5 MB). Returns the handler so callers can remove it again."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "backend.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    return handler


class BusLogHandler(logging.Handler):
    """Publishes every log record as a `log.entry` event and keeps a short history.

    Safe to call from any thread. Never raises into the code that logged.
    """

    def __init__(self, bus: EventBus, loop: asyncio.AbstractEventLoop, history: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self.bus = bus
        self.loop = loop
        self.recent: deque[dict[str, Any]] = deque(maxlen=history)
        self._tasks: set[asyncio.Task[None]] = set()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": mask_secrets(record.getMessage()),
                "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            }
            self.recent.append(entry)
            self.loop.call_soon_threadsafe(self._publish, entry)
        except Exception:  # noqa: BLE001 - logging must never break the app
            self.handleError(record)

    def _publish(self, entry: dict[str, Any]) -> None:
        task = self.loop.create_task(self.bus.publish(Events.LOG_ENTRY, entry))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def mask_secrets(text: str) -> str:
    """Hide credentials embedded in URLs before a log line reaches the UI."""
    import re

    return re.sub(r"//[^@/\s]+@", "//***@", text)
