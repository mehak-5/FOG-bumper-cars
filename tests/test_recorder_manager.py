import asyncio
import json

from backend.app.config import (
    AppConfig,
    CameraConfig,
    FileSource,
    RecordingConfig,
    TriggerConfig,
    VenueInfo,
)
from backend.app.core.events import EventBus, Events
from backend.app.recorder.recorder_manager import RecorderManager
from tests.fakes import Spawner


def make_cfg(tmp_path, **rec):
    base = {
        "open_timeout_seconds": 0.4,
        "disconnect_timeout_seconds": 0.4,
        "max_duration_seconds": 30,
    }
    return AppConfig(
        venue=VenueInfo(name="Test"),
        recording=RecordingConfig(**{**base, **rec}),
        trigger=TriggerConfig(),
        sessions_dir=tmp_path / "sessions",
        cameras=[
            CameraConfig(id="camera_1", name="Front", source=FileSource(path="a.mp4")),
            CameraConfig(id="camera_2", name="Rear", source=FileSource(path="b.mp4")),
        ],
        disk_warn_gb=5,
    )


class Env:
    def __init__(self, tmp_path, behaviors=None, preview=None, **rec):
        self.bus = EventBus()
        self.spawner = Spawner(behaviors)
        self.events: list[tuple[str, dict]] = []
        for topic in (
            Events.RIDE_STARTED,
            Events.RIDE_FINISHED,
            Events.CAMERA_LOST,
            Events.APP_NOTICE,
        ):
            self.bus.subscribe(topic, lambda p, t=topic: self.events.append((t, p)))
        self.mgr = RecorderManager(
            make_cfg(tmp_path, **rec),
            self.bus,
            spawn_fn=self.spawner,
            preview=preview,
            camera_opts={"stop_timeout": 0.3, "poll_interval": 0.05},
        )

    def topics(self):
        return [t for t, _ in self.events]

    def session_json(self):
        (d,) = list(self.mgr.cfg.sessions_dir.iterdir())
        return json.loads((d / "session.json").read_text())


async def test_full_ride_completes_and_writes_session_json(tmp_path):
    e = Env(tmp_path)
    await e.mgr.start_ride()
    assert e.mgr.ride == "recording"
    assert e.session_json()["status"] == "recording"  # trace for crash recovery
    await e.mgr.end_ride({"reason": "trigger"})
    assert e.mgr.ride == "idle"
    meta = e.session_json()
    assert meta["status"] == "completed" and meta["stop_reason"] == "trigger"
    assert [c["status"] for c in meta["cameras"]] == ["completed", "completed"]
    assert all(c["offset_sec"] is not None and c["offset_sec"] >= 0 for c in meta["cameras"])
    assert meta["end_time"] and meta["duration_seconds"] >= 0
    assert e.topics().count(Events.RIDE_STARTED) == 1
    assert e.topics()[-1] == Events.RIDE_FINISHED
    assert e.mgr.last_session["status"] == "completed"
    assert not list(e.mgr.cfg.sessions_dir.rglob("*.tmp"))


async def test_one_unavailable_camera_gives_partial_ride(tmp_path):
    e = Env(tmp_path, {"camera_2": "silent"})
    await e.mgr.start_ride()
    assert e.mgr.ride == "recording"
    assert any(t == Events.APP_NOTICE and p["level"] == "warning" for t, p in e.events)
    await e.mgr.end_ride()
    meta = e.session_json()
    assert meta["status"] == "partial"
    assert {c["id"]: c["status"] for c in meta["cameras"]} == {
        "camera_1": "completed",
        "camera_2": "unavailable",
    }


async def test_spawn_error_camera_does_not_block_other(tmp_path):
    e = Env(tmp_path, {"camera_1": "raise"})
    await e.mgr.start_ride()
    await e.mgr.end_ride()
    assert e.session_json()["status"] == "partial"


async def test_no_camera_aborts_ride_as_failed(tmp_path):
    e = Env(tmp_path, {"camera_1": "silent", "camera_2": "raise"})
    await e.mgr.start_ride()
    assert e.mgr.ride == "idle"
    assert e.session_json()["status"] == "failed"
    assert e.events[-1] == (
        Events.RIDE_FINISHED,
        {"session_id": e.mgr.last_session["id"], "status": "failed"},
    )
    assert Events.RIDE_STARTED not in e.topics()


async def test_mid_ride_disconnect_publishes_camera_lost_and_ride_goes_partial(tmp_path):
    e = Env(tmp_path, {"camera_2": "stall"})
    await e.mgr.start_ride()
    for _ in range(40):
        await asyncio.sleep(0.05)
        if Events.CAMERA_LOST in e.topics():
            break
    lost = [p for t, p in e.events if t == Events.CAMERA_LOST]
    assert lost and lost[0]["camera_id"] == "camera_2"
    assert e.mgr.ride == "recording"  # the other camera keeps recording
    await e.mgr.end_ride()
    assert e.session_json()["status"] == "partial"


async def test_all_cameras_lost_ends_ride_automatically(tmp_path):
    e = Env(tmp_path, {"camera_1": "stall", "camera_2": "stall"})
    await e.mgr.start_ride()
    for _ in range(60):
        await asyncio.sleep(0.05)
        if e.mgr.ride == "idle":
            break
    assert e.mgr.ride == "idle"
    meta = e.session_json()
    assert meta["stop_reason"] == "all_cameras_lost"
    assert meta["status"] in ("failed", "partial")


async def test_time_limit_auto_stops(tmp_path):
    e = Env(tmp_path, ride_duration_seconds=0.3)
    await e.mgr.start_ride()
    for _ in range(20):
        await asyncio.sleep(0.05)
        if e.mgr.ride == "idle":
            break
    assert e.mgr.ride == "idle"
    assert e.session_json()["stop_reason"] == "time_limit"
    assert e.session_json()["status"] == "completed"


async def test_start_while_active_and_end_while_idle_are_ignored(tmp_path):
    e = Env(tmp_path)
    await e.mgr.end_ride()  # nothing running
    await e.mgr.start_ride()
    await e.mgr.start_ride()  # ignored
    assert len(list(e.mgr.cfg.sessions_dir.iterdir())) == 1
    await e.mgr.end_ride()


async def test_end_for_other_session_is_ignored(tmp_path):
    e = Env(tmp_path)
    await e.mgr.start_ride()
    await e.mgr.end_ride({"reason": "time_limit", "session_id": "stale"})
    assert e.mgr.ride == "recording"
    await e.mgr.end_ride()


async def test_concurrent_start_and_end_are_serialized(tmp_path):
    e = Env(tmp_path)
    await asyncio.gather(e.mgr.start_ride(), e.mgr.end_ride(), e.mgr.start_ride())
    # end ran before the first start finished -> ignored; second start ignored -> still recording
    assert e.mgr.ride == "recording"
    await e.mgr.end_ride()
    assert e.mgr.ride == "idle"


async def test_bus_commands_drive_the_manager(tmp_path):
    e = Env(tmp_path)
    await e.bus.publish(Events.RIDE_START, {"source": "keyboard"})
    assert e.mgr.ride == "recording"
    await e.bus.publish(Events.RIDE_END, {"source": "keyboard"})
    assert e.mgr.ride == "idle"


class FakePreview:
    def __init__(self, ready=True):
        self.ready = ready
        self.recording: list[tuple[str, bool]] = []

    def get_readiness(self):
        return {
            "camera_1": {"ready": self.ready, "reason": "no frame yet"},
            "camera_2": {"ready": True, "reason": "ok"},
        }

    def is_ready(self, cid):
        return self.ready or cid != "camera_1"

    def set_recording(self, cid, recording):
        self.recording.append((cid, recording))


async def test_readiness_guard_blocks_start(tmp_path):
    p = FakePreview(ready=False)
    e = Env(tmp_path, preview=p)
    await e.mgr.start_ride()
    assert e.mgr.ride == "idle"
    assert not e.mgr.cfg.sessions_dir.exists()
    notice = next(p for t, p in e.events if t == Events.APP_NOTICE)
    assert "Front" in notice["text"] and "no frame yet" in notice["text"]
    assert e.events[-1][1]["status"] == "failed"


async def test_preview_is_paused_during_ride_and_resumed_after(tmp_path):
    p = FakePreview()
    e = Env(tmp_path, preview=p)
    await e.mgr.start_ride()
    assert ("camera_1", True) in p.recording and ("camera_1", False) not in p.recording
    await e.mgr.end_ride()
    assert ("camera_1", False) in p.recording and ("camera_2", False) in p.recording


async def test_notes_saved_then_reset_for_next_ride(tmp_path):
    e = Env(tmp_path)
    await e.mgr.start_ride()
    e.mgr.set_notes("kids birthday")
    await e.mgr.end_ride()
    d = next(e.mgr.cfg.sessions_dir.iterdir())
    assert json.loads((d / "session.json").read_text())["notes"] == "kids birthday"
    await e.mgr.start_ride()
    assert e.mgr.get_state()["notes"] == ""
    await e.mgr.end_ride()


async def test_fatal_startup_error_fails_ride_without_session(tmp_path):
    e = Env(tmp_path)
    e.mgr._fatal = "ffmpeg missing"
    await e.mgr.start_ride()
    assert e.mgr.ride == "idle"
    assert any("ffmpeg missing" in p.get("text", "") for t, p in e.events if t == Events.APP_NOTICE)
    assert e.events[-1][0] == Events.RIDE_FINISHED


async def test_state_snapshot_and_ride_ends_at(tmp_path):
    e = Env(tmp_path, ride_duration_seconds=5)
    st = e.mgr.get_state()
    assert st["ride"] == "idle" and st["ride_ends_at"] is None
    assert [c["status"] for c in st["cameras"]] == ["idle", "idle"]
    await e.mgr.start_ride()
    assert e.mgr.get_state()["ride_ends_at"] is not None
    await e.mgr.end_ride()
    assert e.mgr.get_state()["ride_ends_at"] is None


async def test_kill_all_kills_running_processes(tmp_path):
    e = Env(tmp_path)
    await e.mgr.start_ride()
    e.mgr.kill_all()
    assert all(p.killed for p in e.spawner.procs.values())
    await e.mgr.end_ride()
