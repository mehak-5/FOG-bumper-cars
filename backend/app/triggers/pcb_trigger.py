"""PCB trigger stub - hardware not implemented until Phase 5.

When serial/TCP/GPIO input is wired, this class must publish Events.RIDE_START and
Events.RIDE_END on the shared EventBus (obeying debounce_seconds) and nothing else.
"""

from __future__ import annotations

from backend.app.core.events import EventBus


class PcbTrigger:
    def __init__(self, bus: EventBus, debounce_seconds: float = 0.5) -> None:
        self.bus = bus
        self.debounce_seconds = debounce_seconds

    def start(self) -> None:
        raise NotImplementedError("PCB trigger arrives in Phase 5")

    def stop(self) -> None:
        pass
