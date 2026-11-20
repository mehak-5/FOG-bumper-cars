import asyncio

import pytest

from backend.app.core.events import EventBus, Events
from backend.app.triggers.keyboard_trigger import KeyboardTrigger
from backend.app.triggers.pcb_trigger import PcbTrigger


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(debounce=0.5):
    bus = EventBus()
    clock = Clock()
    states, cmds, notices = [], [], []
    for topic in (Events.RIDE_START, Events.RIDE_END, Events.APP_QUIT):
        bus.subscribe(topic, lambda p, t=topic: cmds.append((t, p)))
    bus.subscribe(Events.APP_NOTICE, notices.append)
    trig = KeyboardTrigger(bus, debounce, on_trigger_state=states.append, clock=clock)
    return bus, trig, clock, states, cmds, notices


async def test_s_starts_and_e_ends():
    bus, trig, clock, states, cmds, _ = make()
    assert await trig.handle_key("s") is True
    clock.t += 1
    assert await trig.handle_key("e") is True
    assert cmds == [
        (Events.RIDE_START, {"source": "keyboard"}),
        (Events.RIDE_END, {"reason": "trigger", "source": "keyboard"}),
    ]
    assert states[-1]["last_accepted"] is True


async def test_keys_are_case_insensitive_and_others_ignored():
    _, trig, _, states, cmds, _ = make()
    assert await trig.handle_key("x") is True
    assert await trig.handle_key("S") is True
    assert [c[0] for c in cmds] == [Events.RIDE_START]
    assert len(states) == 1


async def test_debounce_blocks_duplicate_press_then_allows_after_window():
    bus, trig, clock, states, cmds, _ = make(debounce=0.5)
    await trig.handle_key("s")
    clock.t += 0.2
    await trig.handle_key("s")
    assert [c[0] for c in cmds] == [Events.RIDE_START]
    assert states[-1] == {
        "source": "keyboard",
        "last_key": "s",
        "last_accepted": False,
        "last_reason": "debounced",
    }
    clock.t += 1
    await trig.handle_key("e")
    await bus.publish(Events.RIDE_FINISHED, {})  # the recorder confirms the ride ended
    clock.t += 1
    await trig.handle_key("s")
    assert [c[0] for c in cmds].count(Events.RIDE_START) == 2


async def test_second_start_while_active_is_rejected():
    _, trig, clock, states, cmds, notices = make()
    await trig.handle_key("s")
    clock.t += 1
    await trig.handle_key("s")
    assert [c[0] for c in cmds] == [Events.RIDE_START]
    assert states[-1]["last_reason"] == "ride already active"
    assert any(n["level"] == "warning" for n in notices)


async def test_end_without_active_ride_is_noted_not_published():
    _, trig, _, states, cmds, _ = make()
    await trig.handle_key("e")
    assert cmds == []
    assert states[-1]["last_reason"] == "no active ride"


async def test_ride_finished_event_resets_active_flag():
    bus, trig, clock, _, cmds, _ = make()
    await trig.handle_key("s")
    await bus.publish(Events.RIDE_FINISHED, {"status": "completed"})  # e.g. auto-stop
    clock.t += 1
    await trig.handle_key("s")
    assert [c[0] for c in cmds].count(Events.RIDE_START) == 2


async def test_q_while_active_ends_ride_then_quits():
    _, trig, _, _, cmds, _ = make()
    await trig.handle_key("s")
    assert await trig.handle_key("q") is False
    assert [c[0] for c in cmds] == [Events.RIDE_START, Events.RIDE_END, Events.APP_QUIT]
    assert cmds[1][1]["reason"] == "quit"


async def test_q_when_idle_just_quits():
    _, trig, _, states, cmds, _ = make()
    assert await trig.handle_key("q") is False
    assert [c[0] for c in cmds] == [Events.APP_QUIT]
    assert states[-1]["last_reason"] == "quit"


async def test_concurrent_start_presses_publish_once():
    _, trig, _, _, cmds, _ = make(debounce=0)
    await asyncio.gather(trig.handle_key("s"), trig.handle_key("s"), trig.handle_key("s"))
    assert [c[0] for c in cmds] == [Events.RIDE_START]


def test_stop_without_start_is_safe():
    bus = EventBus()
    KeyboardTrigger(bus).stop()


def test_pcb_stub_is_inert_until_phase_5():
    pcb = PcbTrigger(EventBus())
    pcb.stop()
    with pytest.raises(NotImplementedError):
        pcb.start()
