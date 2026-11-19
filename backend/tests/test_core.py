import asyncio
import os
import shutil
from pathlib import Path
import pytest

from app.core.config import AppConfig, CameraConfig, RecordingConfig
from app.core.event_bus import EventBus
from app.core.state import RideStatus
from app.recorder.manager import RecorderManager
from app.recorder.preview import PreviewManager
from app.storage.session_store import SessionStore, recover_interrupted_sessions, write_json_atomic


@pytest.fixture
def temp_dir(tmp_path: Path):
    yield tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_event_bus_isolated_subscribers():
    bus = EventBus()
    received = []

    async def faulty_sub(name, payload):
        raise ValueError("Boom")

    async def normal_sub(name, payload):
        received.append((name, payload))

    bus.subscribe("test.event", faulty_sub)
    bus.subscribe("test.event", normal_sub)

    await bus.publish("test.event", {"hello": "world"})
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0][1]["hello"] == "world"


@pytest.mark.asyncio
async def test_session_store_atomic_write_and_recovery(temp_dir: Path):
    sessions_dir = temp_dir / "sessions"
    store = SessionStore(str(sessions_dir))

    # Test atomic write
    session_dir = store.create_session_dir("test_01")
    meta_path = session_dir / "session.json"
    write_json_atomic(meta_path, {"session_id": "test_01", "status": "recording"})

    assert meta_path.is_file()
    assert (session_dir / "session.json.tmp").exists() is False

    # Test crash recovery
    recovered_count = recover_interrupted_sessions(sessions_dir)
    assert recovered_count == 1

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["status"] == "interrupted"
    assert sessions[0]["stop_reason"] == "crash_recovery"
