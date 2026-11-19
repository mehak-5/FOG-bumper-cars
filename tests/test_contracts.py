from pathlib import Path

import pytest

from backend.app.core.events import EventBus
from backend.app.core.models import (
    CLIENT_MESSAGES,
    SERVER_MESSAGES,
    AppState,
    ProtocolError,
    SessionNotes,
    make_envelope,
    parse_client_message,
)
from backend.app.core.ts_generator import generate, ts_type
from backend.app.recorder.recorder_manager import RecorderManager
from tests.fakes import Spawner
from tests.test_recorder_manager import make_cfg

GENERATED = Path(__file__).resolve().parent.parent / "backend/app/ui/ts/messages.generated.ts"


def test_committed_typescript_is_up_to_date():
    text = GENERATED.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text == generate(), "run: python tools/gen_ts_types.py"


def test_every_message_has_a_typescript_payload():
    ts = generate()
    for name, model in {**CLIENT_MESSAGES, **SERVER_MESSAGES}.items():
        assert f'"{name}": {model.__name__};' in ts
        assert f"export interface {model.__name__}" in ts


def test_spec_message_names():
    assert set(CLIENT_MESSAGES) == {
        "state.get",
        "ride.start",
        "ride.end",
        "session.notes",
        "logs.subscribe",
        "logs.unsubscribe",
        "ping",
    }
    assert set(SERVER_MESSAGES) == {
        "state",
        "notice",
        "preview.frame",
        "camera.readiness",
        "disk.status",
        "log",
        "processing.progress",
        "reel.ready",
        "share.created",
        "tunnel.status",
        "app.quit",
        "error",
        "pong",
    }


async def test_recorder_state_matches_appstate_contract(tmp_path):
    mgr = RecorderManager(
        make_cfg(tmp_path),
        EventBus(),
        spawn_fn=Spawner(),
        camera_opts={"stop_timeout": 0.3, "poll_interval": 0.05},
    )
    AppState.model_validate(mgr.get_state())  # idle
    await mgr.start_ride()
    state = AppState.model_validate(mgr.get_state())
    assert state.ride == "recording" and len(state.cameras) == 2
    await mgr.end_ride()
    state = AppState.model_validate(mgr.get_state())
    assert state.last_session is not None and state.last_session.status == "completed"
    make_envelope("state", mgr.get_state())  # and it is sendable over the socket


def test_make_envelope_shape_and_validation():
    env = make_envelope("notice", {"level": "info", "text": "hi"})
    assert env["type"] == "notice" and env["payload"] == {"level": "info", "text": "hi"}
    assert env["id"] and env["ts"]
    assert make_envelope("pong")["payload"] == {}
    with pytest.raises(ProtocolError):
        make_envelope("nope")
    with pytest.raises(ValueError):
        make_envelope("notice", {"level": "loud", "text": "x"})
    with pytest.raises(ValueError):
        make_envelope("notice", {"level": "info", "text": "x", "extra": 1})


def test_parse_client_message():
    t, p = parse_client_message({"type": "session.notes", "payload": {"notes": "n"}})
    assert t == "session.notes" and p == SessionNotes(notes="n")
    assert parse_client_message({"type": "ping"})[0] == "ping"


@pytest.mark.parametrize(
    "raw",
    [
        "ping",
        [],
        {},
        {"type": "bogus"},
        {"type": "session.notes", "payload": {}},
        {"type": "session.notes", "payload": {"notes": 5}},
        {"type": "ping", "payload": {"x": 1}},
        {"type": "ping", "unexpected": 1},
        {"type": "state", "payload": {}},  # server-only type is rejected from clients
    ],
)
def test_parse_client_message_rejects_bad_input(raw):
    with pytest.raises(ProtocolError):
        parse_client_message(raw)


def test_ts_type_conversion():
    assert ts_type({"type": "string"}) == "string"
    assert ts_type({"anyOf": [{"type": "integer"}, {"type": "null"}]}) == "number | null"
    assert ts_type({"type": "array", "items": {"type": "string"}}) == "string[]"
    assert (
        ts_type({"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}})
        == "(string | null)[]"
    )
    assert ts_type({"enum": ["a", "b"]}) == '"a" | "b"'
    assert (
        ts_type({"type": "object", "additionalProperties": {"type": "boolean"}})
        == "Record<string, boolean>"
    )
    assert ts_type({"$ref": "#/$defs/Foo"}) == "Foo"
