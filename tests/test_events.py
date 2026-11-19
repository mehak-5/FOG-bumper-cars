import asyncio
import logging

import pytest

from backend.app.core.events import EventBus, Events


async def test_delivers_payload_to_sync_and_async_handlers():
    bus = EventBus()
    got: list[tuple[str, object]] = []

    async def a(p):
        got.append(("a", p))

    bus.subscribe(Events.RIDE_START, a)
    bus.subscribe(Events.RIDE_START, lambda p: got.append(("b", p)))
    await bus.publish(Events.RIDE_START, {"source": "keyboard"})
    assert sorted(got) == [("a", {"source": "keyboard"}), ("b", {"source": "keyboard"})]


async def test_default_payload_is_empty_dict():
    bus = EventBus()
    got = []
    bus.subscribe(Events.RIDE_END, got.append)
    await bus.publish(Events.RIDE_END)
    assert got == [{}]


async def test_failing_handler_is_isolated_and_logged(caplog):
    bus = EventBus()
    ok = []

    def bad(_):
        raise RuntimeError("boom")

    async def bad_async(_):
        raise RuntimeError("boom2")

    bus.subscribe(Events.RIDE_END, bad)
    bus.subscribe(Events.RIDE_END, bad_async)
    bus.subscribe(Events.RIDE_END, ok.append)
    with caplog.at_level(logging.ERROR, logger="bus"):
        await bus.publish(Events.RIDE_END, 1)  # must not raise
    assert ok == [1]
    assert caplog.text.count("Handler failed") == 2


async def test_publish_waits_for_all_handlers():
    bus = EventBus()
    done = []

    async def slow(_):
        await asyncio.sleep(0.05)
        done.append(1)

    bus.subscribe(Events.RIDE_STARTED, slow)
    await bus.publish(Events.RIDE_STARTED)
    assert done == [1]


async def test_handlers_run_concurrently():
    bus = EventBus()
    order = []

    async def first(_):
        order.append("first-start")
        await asyncio.sleep(0.05)
        order.append("first-end")

    async def second(_):
        order.append("second")

    bus.subscribe(Events.RIDE_STARTED, first)
    bus.subscribe(Events.RIDE_STARTED, second)
    await bus.publish(Events.RIDE_STARTED)
    assert order.index("second") < order.index("first-end")


async def test_unsubscribe():
    bus = EventBus()
    got = []
    off = bus.subscribe(Events.APP_NOTICE, got.append)
    await bus.publish(Events.APP_NOTICE, 1)
    off()
    off()  # idempotent
    await bus.publish(Events.APP_NOTICE, 2)
    assert got == [1]


async def test_handler_can_unsubscribe_during_publish():
    bus = EventBus()
    got = []
    holder = {}

    def once(p):
        got.append(p)
        holder["off"]()

    holder["off"] = bus.subscribe(Events.APP_NOTICE, once)
    await bus.publish(Events.APP_NOTICE, 1)
    await bus.publish(Events.APP_NOTICE, 2)
    assert got == [1]


async def test_publish_without_subscribers_is_noop():
    await EventBus().publish("anything")


async def test_strict_mode_rejects_unknown_topics():
    bus = EventBus(strict=True)
    with pytest.raises(ValueError):
        bus.subscribe("ride.strat", lambda p: None)
    with pytest.raises(ValueError):
        await bus.publish("nope")
    bus.subscribe(Events.RIDE_START, lambda p: None)  # known topics are fine


def test_event_names_match_spec():
    assert Events.all() == {
        "ride.start", "ride.end", "app.quit",
        "ride.started", "ride.finished", "state.changed", "camera.lost",
        "processing.queued", "processing.progress", "moments.ready", "reel.ready",
        "processing.failed", "share.created", "tunnel.up", "tunnel.down",
        "app.notice", "log.entry",
    }  # fmt: skip
