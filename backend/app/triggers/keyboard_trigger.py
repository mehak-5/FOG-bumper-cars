"""Keyboard trigger: S/E/Q key presses -> ride.start / ride.end / app.quit events.

It knows nothing about cameras. A PCB trigger publishes the same events. Port of
src/backend/triggers/keyboardTrigger.ts; global key capture uses pynput (spec).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from backend.app.core.events import EventBus, Events

log = logging.getLogger("trigger")

TriggerStateCb = Callable[[dict[str, Any]], None]


class KeyboardTrigger:
    def __init__(
        self,
        bus: EventBus,
        debounce_seconds: float = 0.5,
        *,
        on_trigger_state: TriggerStateCb | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus = bus
        self.debounce_seconds = debounce_seconds
        self._on_state = on_trigger_state
        self._clock = clock
        self._active = False
        self._last_press: dict[str, float] = {}
        self._listener: Any = None
        # Also fires on auto-stop (time limit) or a failed start.
        bus.subscribe(Events.RIDE_FINISHED, self._ride_finished)

    def _ride_finished(self, _data: Any) -> None:
        self._active = False

    async def _say(self, level: str, text: str) -> None:
        (log.info if level == "info" else log.warning)(text)
        await self.bus.publish(Events.APP_NOTICE, {"level": level, "text": text})

    def _emit_state(self, key: str, accepted: bool, reason: str) -> None:
        if self._on_state:
            self._on_state(
                {
                    "source": "keyboard",
                    "last_key": key,
                    "last_accepted": accepted,
                    "last_reason": reason,
                }
            )

    async def handle_key(self, key: str) -> bool:
        """Handle one key. Returns False when the operator asked to quit."""
        key = key.lower()
        if key not in ("s", "e", "q"):
            return True
        now = self._clock()
        last = self._last_press.get(key)
        if last is not None and now - last < self.debounce_seconds:
            await self._say("info", f"Duplicate '{key.upper()}' press ignored")
            self._emit_state(key, False, "debounced")
            return True
        self._last_press[key] = now

        if key == "s":
            if self._active:
                await self._say("warning", "A ride is already active - press E to end it")
                self._emit_state(key, False, "ride already active")
            else:
                self._active = True  # set before publishing so a second press cannot slip through
                await self._say("info", "Starting ride...")
                await self.bus.publish(Events.RIDE_START, {"source": "keyboard"})
                self._emit_state(key, True, "ride start published")
            return True

        if self._active:
            reason = "quit" if key == "q" else "trigger"
            await self._say("info", "Ending ride...")
            await self.bus.publish(Events.RIDE_END, {"reason": reason, "source": "keyboard"})
            self._emit_state(key, True, reason)
        elif key == "e":
            await self._say("info", "No active ride to end")
            self._emit_state(key, False, "no active ride")
        if key == "q":
            log.info("Exiting...")
            if not self._active:
                self._emit_state(key, True, "quit")
            await self.bus.publish(Events.APP_QUIT, {"source": "keyboard"})
            return False
        return True

    # ---------------- global key capture (pynput) ----------------
    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Begin listening for global key presses. pynput calls us from its own thread."""
        from pynput import keyboard  # imported lazily: not needed in tests or headless runs

        loop = loop or asyncio.get_running_loop()

        def on_press(k: Any) -> None:
            char = getattr(k, "char", None)
            if char:
                asyncio.run_coroutine_threadsafe(self.handle_key(char), loop)

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
