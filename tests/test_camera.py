import asyncio

import pytest

from backend.app.config import (
    CameraConfig,
    FileSource,
    RecordingConfig,
    StreamSource,
    WebcamSource,
)
from backend.app.recorder.camera import CameraRecorder, build_args, describe_source, mask
from backend.app.recorder.devices import parse_dshow

REC = RecordingConfig(open_timeout_seconds=0.3, disconnect_timeout_seconds=0.3)


class FakeStdin:
    def __init__(self, proc):
        self.proc = proc
        self.writes: list[bytes] = []

    def write(self, data):
        self.writes.append(data)
        if data == b"q\n" and self.proc.exit_on_q:
            self.proc.finish(0)

    async def drain(self):
        pass


class FakeProc:
    """Stands in for asyncio.subprocess.Process."""

    def __init__(self, out_path, exit_on_q=True):
        self.out_path = out_path
        self.exit_on_q = exit_on_q
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self)
        self.returncode = None
        self._done = asyncio.Event()
        self.killed = False

    def progress(self, frames):
        self.stdout.feed_data(f"frame={frames}\nprogress=continue\n".encode())

    def finish(self, code):
        if self.returncode is None:
            self.returncode = code
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.finish(-9)


def make(tmp_path, *, exit_on_q=True, rec=REC, spawn_error=None):
    out = tmp_path / "camera_1.mp4"
    holder = {}

    async def spawn(args, cam):
        if spawn_error:
            raise spawn_error
        holder["args"] = args
        holder["proc"] = FakeProc(out, exit_on_q)
        return holder["proc"]

    cam = CameraConfig(id="camera_1", name="Front", source=FileSource(path="in.mp4"))
    r = CameraRecorder(cam, rec, out, spawn_fn=spawn, stop_timeout=0.3, poll_interval=0.05)
    return r, holder, out


async def started(tmp_path, **kw):
    r, h, out = make(tmp_path, **kw)
    task = asyncio.create_task(r.start())
    while "proc" not in h:
        await asyncio.sleep(0.01)
    return r, h, out, task


# ---------- args ----------
async def test_args_have_crash_safe_flags_for_dshow_webcam():
    cam = CameraConfig(id="a", name="A", source=WebcamSource(name="Cam"))
    args = await build_args(cam, RecordingConfig(), "out.mp4", platform="win32")
    joined = " ".join(args)
    assert "-movflags +frag_keyframe+empty_moov+default_base_moof" in joined
    assert "-rtbufsize 256M" in joined
    assert "-use_wallclock_as_timestamps 1" in joined
    assert "-f dshow -i video=Cam" in joined
    assert "scale=1280:720,fps=25" in joined
    assert "-an" in args and args[-1] == "out.mp4"
    # input options must come before -i
    assert args.index("-rtbufsize") < args.index("-i")
    # GOP=1: every frame is a keyframe so every fragment is independently
    # decodable – a hard kill mid-ride still leaves a playable file.
    assert args[args.index("-g") + 1] == "1"
    # -flush_packets 1: each packet write is immediately handed to the OS so a
    # process kill cannot lose data sitting in FFmpeg's internal write buffer.
    assert args[args.index("-flush_packets") + 1] == "1"


async def test_args_for_stream_file_and_linux():
    rtsp = CameraConfig(id="a", name="A", source=StreamSource(url="rtsp://u:p@h/s"))
    a = await build_args(rtsp, RecordingConfig(), "o.mp4", platform="win32")
    assert ["-rtsp_transport", "tcp"] == a[
        a.index("-rtsp_transport") : a.index("-rtsp_transport") + 2
    ]
    assert "-use_wallclock_as_timestamps" in a

    f = CameraConfig(id="a", name="A", source=FileSource(path="x.mp4"))
    a = await build_args(f, RecordingConfig(), "o.mp4")
    assert "-re" in a and "-use_wallclock_as_timestamps" not in a

    w = CameraConfig(id="a", name="A", source=WebcamSource(index=1))
    a = await build_args(w, RecordingConfig(), "o.mp4", platform="linux")
    assert "/dev/video1" in a and "v4l2" in a


async def test_non_mp4_format_omits_movflags():
    f = CameraConfig(id="a", name="A", source=FileSource(path="x.mkv"))
    a = await build_args(f, RecordingConfig(format="mkv"), "o.mkv")
    assert "-movflags" not in a


async def test_gop_size_is_one_for_all_source_types():
    """GOP=1 (every frame is a keyframe) means every fragment is independently
    decodable. A hard kill mid-ride leaves a valid fragmented MP4 up to the
    last-written frame, satisfying the 'recording is sacred' crash-safety rule.
    -flush_packets 1 ensures each packet write is immediately handed to the OS.
    """
    for src, plat in (
        (WebcamSource(name="Cam"), "win32"),
        (StreamSource(url="rtsp://h/s"), "linux"),
        (FileSource(path="x.mp4"), "linux"),
    ):
        cam = CameraConfig(id="a", name="A", source=src)
        args = await build_args(cam, RecordingConfig(), "o.mp4", platform=plat)
        assert args[args.index("-g") + 1] == "1", f"Expected -g 1 for source {src}"
        assert args[args.index("-flush_packets") + 1] == "1", f"Missing -flush_packets 1 for {src}"


def test_mask_and_describe():
    assert mask("rtsp://user:pw@host/x") == "rtsp://***@host/x"
    assert "pw" not in describe_source(StreamSource(url="rtsp://user:pw@h/s"))
    assert describe_source(WebcamSource(index=2)) == "webcam #2"


def test_parse_dshow():
    text = '[dshow] "Cam A" (video)\n[dshow] "Mic" (audio)\n[dshow] "Cam A" (video)\n[dshow] "B" (video)'
    assert parse_dshow(text) == ["Cam A", "B"]


# ---------- lifecycle ----------
async def test_happy_path_graceful_stop_with_q(tmp_path):
    r, h, out, task = await started(tmp_path)
    out.write_bytes(b"data")
    h["proc"].progress(5)
    assert await task is True
    assert r.status == "recording" and r.first_frame_at is not None
    await r.stop()
    assert h["proc"].stdin.writes == [b"q\n"]
    assert not h["proc"].killed
    assert r.status == "completed"
    assert out.exists()
    assert r.state().file == "camera_1.mp4"


async def test_open_timeout_marks_unavailable_and_kills(tmp_path):
    r, h, out, task = await started(tmp_path)
    assert await task is False
    assert r.status == "unavailable"
    assert "No video received" in r.error
    assert h["proc"].killed


async def test_stall_marks_failed(tmp_path):
    r, h, out, task = await started(tmp_path)
    out.write_bytes(b"data")
    h["proc"].progress(3)
    assert await task is True
    await asyncio.sleep(0.7)
    assert r.status == "failed"
    assert "disconnected" in r.error
    assert h["proc"].killed
    assert out.exists()  # frames were written, keep the playable fragments


async def test_unexpected_exit_while_recording_fails(tmp_path):
    r, h, out, task = await started(tmp_path)
    h["proc"].progress(2)
    await task
    h["proc"].stderr.feed_data(b"device lost\n")
    h["proc"].finish(1)
    await r._exited.wait()
    assert r.status == "failed"
    assert "exit code 1" in r.error and "device lost" in r.error


async def test_exit_before_first_frame_is_unavailable_and_cleans_up(tmp_path):
    r, h, out, task = await started(tmp_path)
    out.write_bytes(b"")
    h["proc"].stderr.feed_data(b"Could not find video device\n")
    h["proc"].finish(1)
    assert await task is False
    assert r.status == "unavailable"
    assert "Could not find video device" in r.error
    assert not out.exists()


async def test_hung_ffmpeg_is_force_killed_after_timeout(tmp_path):
    r, h, out, task = await started(tmp_path, exit_on_q=False)
    h["proc"].progress(4)
    await task
    await r.stop()
    assert h["proc"].stdin.writes == [b"q\n"]
    assert h["proc"].killed


async def test_zero_byte_file_is_deleted(tmp_path):
    r, h, out, task = await started(tmp_path)
    h["proc"].progress(1)
    await task
    out.write_bytes(b"")
    await r.stop()
    assert not out.exists()


async def test_spawn_failure_is_unavailable_and_does_not_raise(tmp_path):
    r, h, out = make(tmp_path, spawn_error=FileNotFoundError("ffmpeg not found"))
    assert await r.start() is False
    assert r.status == "unavailable" and "ffmpeg not found" in r.error
    await r.stop()  # safe no-op


async def test_stop_is_idempotent(tmp_path):
    r, h, out, task = await started(tmp_path)
    h["proc"].progress(1)
    await task
    out.write_bytes(b"x")
    await r.stop()
    await r.stop()
    assert h["proc"].stdin.writes == [b"q\n"]


async def test_on_change_fires(tmp_path):
    seen = []
    r, h, out = make(tmp_path)
    r._on_change = lambda: seen.append(r.status)
    task = asyncio.create_task(r.start())
    while "proc" not in h:
        await asyncio.sleep(0.01)
    h["proc"].progress(1)
    await task
    assert seen[0] == "connecting" and "recording" in seen
    out.write_bytes(b"x")
    await r.stop()


@pytest.mark.parametrize("frames", [0])
async def test_no_frames_at_stop_after_recording_status_cannot_happen(tmp_path, frames):
    # a recorder that never saw frames is reported unavailable, never "completed"
    r, h, out, task = await started(tmp_path)
    h["proc"].finish(0)
    assert await task is False
    assert r.status != "completed"
