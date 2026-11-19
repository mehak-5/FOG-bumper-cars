import asyncio
import logging
import time
from typing import Callable, Optional
from pynput import keyboard

from app.core.event_bus import EventBus
from app.core.state import TriggerState

logger = logging.getLogger("triggers")


class KeyboardTrigger:
    """Listens for global hotkeys (S=start, E=end, Q=quit) with a debounce guard."""

    def __init__(
        self,
        bus: EventBus,
        debounce_seconds: float = 0.5,
        on_trigger_state: Optional[Callable[[TriggerState], None]] = None,
    ) -> None:
        self.bus = bus
        self.debounce_seconds = debounce_seconds
        self.on_trigger_state = on_trigger_state or (lambda _: None)

        self._last_key: Optional[str] = None
        self._last_time: float = 0.0
        self._listener: Optional[keyboard.Listener] = None

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()
        logger.info("KeyboardTrigger active (Hotkeys: S=Start, E=End, Q=Quit)")

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        char: Optional[str] = None
        if hasattr(key, "char") and key.char:
            char = key.char.lower()
        elif key == keyboard.Key.esc:
            char = "q"

        if char in ["s", "e", "q"]:
            asyncio.run(self.handle_key(char))

    async def handle_key(self, char: str) -> bool:
        now = time.time()
        # Debounce repeated identical keys
        if self._last_key == char and (now - self._last_time) < self.debounce_seconds:
            logger.debug("Debounced repeated key '%s'", char)
            self.on_trigger_state(
                TriggerState(
                    source="keyboard",
                    last_key=char.upper(),
                    last_accepted=False,
                    last_reason="debounce",
                )
            )
            return True

        self._last_key = char
        self._last_time = now

        if char == "s":
            logger.info("Trigger 'S' -> ride.start")
            self.on_trigger_state(
                TriggerState(
                    source="keyboard",
                    last_key="S",
                    last_accepted=True,
                    last_reason="start_requested",
                )
            )
            await self.bus.publish("ride.start", {"source": "keyboard"})
            return True

        elif char == "e":
            logger.info("Trigger 'E' -> ride.end")
            self.on_trigger_state(
                TriggerState(
                    source="keyboard",
                    last_key="E",
                    last_accepted=True,
                    last_reason="end_requested",
                )
            )
            await self.bus.publish("ride.end", {"source": "keyboard", "reason": "key_e"})
            return True

        elif char == "q":
            logger.info("Trigger 'Q' -> app.quit")
            self.on_trigger_state(
                TriggerState(
                    source="keyboard",
                    last_key="Q",
                    last_accepted=True,
                    last_reason="quit_requested",
                )
            )
            await self.bus.publish("ride.end", {"source": "keyboard", "reason": "quit"})
            await self.bus.publish("app.quit", {})
            return False

        return True
