import asyncio
from pathlib import Path

from backend.app.config import CameraConfig, FileSource, RecordingConfig, WebcamSource
from backend.app.recorder.preview import PreviewManager, extract_frames, preview_args
from tests.fakes import FakeProc

REC = RecordingConfig()


def jpeg(payload=b"x"):
    return b"\xff\xd8" + payload + b"\xff\xd9"


def cams():
    return [
        CameraConfig(id="camera_1", name="A", source=FileSource(path="a.mp4")),
        CameraConfig(id="camera_2", name="B", source=FileSource(path="b.mp4")),
    ]


class Spawn:
    def __init__(self, fail_first=0):
        self.procs: list[FakeProc] = []
        self.fail_first = fail_first
        self.calls = 0

    async def __call__(self, args, cam):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise FileNotFoundError("no ffmpeg")
        proc = FakeProc(Path("x"))
        proc.cam_id = cam.id
        self.procs.append(proc)
        return proc

    def for_cam(self, cid):
        return [p for p in self.procs if p.cam_id == cid]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def settle(n=5):
    for _ in range(n):
        await asyncio.sleep(0.01)


def make(spawn=None, **kw):
    clock = Clock()
    frames = []
    spawn = spawn or Spawn()
    pm = PreviewManager(
        cams(),
        REC,
        spawn_fn=spawn,
        on_frame=lambda cid, f: frames.append((cid, f)),
        clock=clock,
        backoff_start=0.02,
        backoff_max=0.1,
        **kw,
    )
    return pm, spawn, clock, frames


# ---------- frame extraction ----------
def test_extract_frames_splits_and_keeps_partial():
    buf = bytearray(jpeg(b"1") + jpeg(b"22") + b"\xff\xd8partial")
    assert extract_frames(buf) == [jpeg(b"1"), jpeg(b"22")]
    assert bytes(buf) == b"\xff\xd8partial"
    buf += b"-done\xff\xd9"
    assert extract_frames(buf) == [b"\xff\xd8partial-done\xff\xd9"]
    assert len(buf) == 0


def test_extract_frames_drops_junk_and_handles_chunk_boundaries():
    buf = bytearray(b"junk" + jpeg())
    assert extract_frames(buf) == [jpeg()]
    buf = bytearray(b"no frame start here")
    assert extract_frames(buf) == [] and len(buf) == 0
    buf = bytearray(b"\xff\xd8abc\xff")  # EOI split across chunks
    assert extract_frames(buf) == []
    buf += b"\xd9"
    assert extract_frames(buf) == [b"\xff\xd8abc\xff\xd9"]


# ---------- args ----------
async def test_preview_args():
    cam = CameraConfig(id="a", name="A", source=WebcamSource(name="Cam"))
    a = await preview_args(cam, REC, platform="win32")
    assert "scale=320:180,fps=2" in a and a[-1] == "-"
    assert a[-5:] == ["-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    assert "video=Cam" in a


# ---------- readiness ----------
async def test_readiness_progression_no_frame_live_stale():
    pm, spawn, clock, frames = make()
    pm.start()
    await settle()
    assert pm.get_readiness()["camera_1"] == {
        "ready": False,
        "reason": "no frame yet",
        "last_frame_at": None,
    }
    assert pm.is_ready("camera_1") is False

    spawn.for_cam("camera_1")[0].stdout.feed_data(jpeg(b"hello"))
    await settle()
    r = pm.get_readiness()["camera_1"]
    assert r["ready"] and r["reason"] == "live" and r["last_frame_at"] == 1000.0 * 1000
    assert pm.is_ready("camera_1") and not pm.is_ready("camera_2")
    assert frames == [("camera_1", jpeg(b"hello"))]
    assert pm.get_frame("camera_1") == jpeg(b"hello")
    assert pm.get_frame_data_url("camera_1").startswith("data:image/jpeg;base64,")
    assert pm.get_frame("camera_2") is None and pm.get_frame_data_url("camera_2") is None

    clock.t += 2.5  # frame is now older than 2s
    r = pm.get_readiness()["camera_1"]
    assert not r["ready"] and r["reason"] == "stale frame"
    pm.stop()


def test_unknown_camera_is_not_ready():
    pm, *_ = make()
    assert pm.is_ready("nope") is False and pm.get_frame("nope") is None
    pm.set_recording("nope", True)  # no crash


# ---------- recorder hand-off ----------
async def test_recording_closes_pipe_and_reports_ready_then_reopens():
    pm, spawn, clock, _ = make()
    pm.start()
    await settle()
    first = spawn.for_cam("camera_1")[0]
    first.stdout.feed_data(jpeg())
    await settle()

    pm.set_recording("camera_1", True)
    await settle()
    assert first.killed  # device freed for the recorder
    assert pm.get_readiness()["camera_1"]["reason"] == "recording"
    assert pm.is_ready("camera_1")
    clock.t += 60  # even with a very old frame
    assert pm.is_ready("camera_1")
    n = len(spawn.for_cam("camera_1"))
    await asyncio.sleep(0.15)
    assert len(spawn.for_cam("camera_1")) == n  # no respawn while recording
    assert len(spawn.for_cam("camera_2")) == 1  # other camera untouched

    pm.set_recording("camera_1", False)
    await settle()
    assert len(spawn.for_cam("camera_1")) == n + 1  # reopened
    pm.stop()


async def test_set_recording_is_idempotent():
    pm, spawn, *_ = make()
    pm.start()
    await settle()
    pm.set_recording("camera_1", False)  # not recording -> no-op, no extra pipe
    await settle()
    assert len(spawn.for_cam("camera_1")) == 1
    pm.stop()


# ---------- reconnect ----------
async def test_reconnects_after_pipe_dies():
    pm, spawn, *_ = make()
    pm.start()
    await settle()
    spawn.for_cam("camera_1")[0].finish(1)
    await asyncio.sleep(0.15)
    assert len(spawn.for_cam("camera_1")) >= 2
    pm.stop()


async def test_spawn_failures_retry_with_backoff_then_recover():
    spawn = Spawn(fail_first=3)
    pm, _, _, _ = make(spawn=spawn)
    pm.start()
    await asyncio.sleep(0.6)
    assert spawn.calls > 3 and spawn.procs  # eventually opened despite initial failures
    pm.stop()


async def test_backoff_is_capped_and_reset_on_data():
    pm, spawn, *_ = make()
    pm.start()
    await settle()
    pipe = pm._pipes["camera_1"]
    spawn.for_cam("camera_1")[0].finish(1)
    for _ in range(40):
        await asyncio.sleep(0.01)
        for p in spawn.for_cam("camera_1")[1:]:
            p.finish(1)
    assert pipe.backoff <= 0.1
    pm.stop()


async def test_stop_kills_everything_and_stays_stopped():
    pm, spawn, *_ = make()
    pm.start()
    await settle()
    pm.stop()
    assert all(p.killed for p in spawn.procs)
    n = len(spawn.procs)
    await asyncio.sleep(0.1)
    assert len(spawn.procs) == n
    pm.set_recording("camera_1", True)
    pm.set_recording("camera_1", False)  # must not relaunch after stop()
    await asyncio.sleep(0.05)
    assert len(spawn.procs) == n
