import time

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.app.core.models import AppState
from tests.conftest import WS


def recv_until(ws, msg_type, limit=200):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"no {msg_type!r} message received")


def wait_for(cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_initial_state_is_valid_appstate(client):
    with client.websocket_connect(WS) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "state" and msg["id"] and msg["ts"]
        state = AppState.model_validate(msg["payload"])
        assert state.ride == "idle" and state.venue == "Test Venue"
        assert [c.id for c in state.cameras] == ["camera_1", "camera_2"]


def test_ping_pong_and_state_get(client):
    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ping", "id": "x"})
        assert recv_until(ws, "pong")["payload"] == {}
        ws.send_json({"type": "state.get"})
        assert recv_until(ws, "state")["payload"]["ride"] == "idle"


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "nope"},
        {"type": "session.notes", "payload": {"notes": 1}},
        {"type": "state"},  # server-only type
        [1, 2],
    ],
)
def test_bad_messages_get_error_and_connection_survives(client, raw):
    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json(raw)
        err = recv_until(ws, "error")
        assert err["payload"]["code"] == "protocol"
        ws.send_json({"type": "ping"})
        recv_until(ws, "pong")


def test_invalid_json_gets_error(client):
    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_text("{not json")
        assert recv_until(ws, "error")["payload"]["message"] == "Invalid JSON"
        ws.send_json({"type": "ping"})
        recv_until(ws, "pong")


def test_foreign_origin_is_refused(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS, headers={"origin": "https://evil.example.com"}):
            pass
    assert exc.value.code == 1008
    with client.websocket_connect(WS, headers={"origin": "http://127.0.0.1:8000"}) as ws:
        assert ws.receive_json()["type"] == "state"


def test_ride_start_and_end_over_websocket(client, ctx):
    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ride.start"})
        while True:
            st = recv_until(ws, "state")["payload"]
            if st["ride"] == "recording":
                break
        assert st["trigger"]["source"] == "button"
        ws.send_json({"type": "session.notes", "payload": {"notes": "vip"}})
        assert wait_for(lambda: ctx.recorder.notes == "vip")
        ws.send_json({"type": "ride.end"})
        while True:
            st = recv_until(ws, "state")["payload"]
            if st["ride"] == "idle" and st["last_session"]:
                break
        assert st["last_session"]["status"] == "completed"
        assert st["notes"] == "vip"


def test_logs_are_streamed_only_to_subscribers(client):
    import logging

    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        logging.getLogger("t").error("before subscribe")
        ws.send_json({"type": "logs.subscribe"})
        logging.getLogger("t").error("rtsp://user:secret@cam/x failed")
        seen = []
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] == "log":
                seen.append(msg["payload"]["message"])
                if any("failed" in m for m in seen):
                    break
        joined = " ".join(seen)
        assert "before subscribe" in joined  # history replayed on subscribe
        assert "secret" not in joined and "//***@" in joined  # credentials masked
        ws.send_json({"type": "logs.unsubscribe"})
        ws.send_json({"type": "ping"})


def test_app_quit_event_notifies_clients_and_calls_hook(client, ctx, quits):
    import asyncio

    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        ctx.trigger._active = False
        fut = asyncio.run_coroutine_threadsafe(ctx.trigger.handle_key("q"), _loop_of(client))
        fut.result(5)
        assert recv_until(ws, "app.quit")["payload"] == {}
    assert quits == [True]


def _loop_of(client):
    return (
        client.portal._event_loop
        if hasattr(client.portal, "_event_loop")
        else client.portal.call(_get_loop)
    )


async def _get_loop():
    import asyncio

    return asyncio.get_running_loop()
