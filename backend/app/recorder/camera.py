"""One CameraRecorder = one ffmpeg process = one output file. Cameras never share state.

Port of src/backend/managers/camera.ts with the crash-safety flags from the spec:
fragmented MP4, wall-clock timestamps, a large DirectShow buffer and a clean `q` shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.config import (
    CameraConfig,
    FileSource,
    RecordingConfig,
    Source,
    StreamSource,
    WebcamSource,
)
from backend.app.recorder.devices import list_webcams

log = logging.getLogger("camera")

STOP_TIMEOUT_S = 5.0  # wait this long after `q` before force-killing
# +frag_keyframe: flush a new moof atom at each keyframe boundary.
# +empty_moov:    write an empty moov at t=0 so a partial file is streamable.
# +default_base_moof: interoperable base_data_offset.
# -g 1:           every frame is a keyframe, so every frame starts a new
#                 independently decodable fragment.  A hard kill mid-ride
#                 leaves a valid fragmented MP4 up to the last flush.
# -flush_packets 1: force OS-level write() after each packet so a process
#                 kill cannot lose data that is only in FFmpeg's write buffer.
FRAGMENTED_MP4 = "+frag_keyframe+empty_moov+default_base_moof"
GOP_SIZE = 1  # 1 = every frame is I-frame; crash-safe, slightly larger file

SpawnFn = Callable[[list[str], CameraConfig], Awaitable[Any]]
_CREDENTIALS = re.compile(r"//[^@/\s]+@")
_FRAME = re.compile(r"^frame=(\d+)")


def mask(text: object) -> str:
    """Hide RTSP credentials in logs."""
    return _CREDENTIALS.sub("//***@", str(text))


def describe_source(s: Source) -> str:
    if isinstance(s, WebcamSource):
        return f'webcam "{s.name}"' if s.name else f"webcam #{s.index}"
    if isinstance(s, StreamSource):
        return mask(s.url)
    return s.path


async def default_spawn(ffmpeg_path: str, args: list[str]) -> asyncio.subprocess.Process:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return await asyncio.create_subprocess_exec(
        ffmpeg_path,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )


async def input_args(cam: CameraConfig, rec: RecordingConfig, platform: str) -> list[str]:
    extra = list(cam.input_args or [])
    src = cam.source
    # Live sources: stamp frames with the wall clock so cameras stay aligned on one timeline.
    live = ["-use_wallclock_as_timestamps", "1"]
    if isinstance(src, WebcamSource):
        if platform == "win32":
            name = src.name
            if name is None:
                names = await list_webcams(rec.ffmpeg_path)
                idx = src.index or 0
                name = names[idx] if idx < len(names) else None
            if not name:
                raise RuntimeError(
                    f"Webcam #{src.index} not found (run: python tools/list_cameras.py)"
                )
            return [*extra, "-rtbufsize", "256M", *live, "-f", "dshow", "-i", f"video={name}"]
        return [*extra, *live, "-f", "v4l2", "-i", src.name or f"/dev/video{src.index}"]
    if isinstance(src, StreamSource):
        rtsp = src.url.lower().startswith("rtsp")
        opts = (
            ["-rtsp_transport", "tcp", "-timeout", str(int(rec.open_timeout_seconds * 1e6))]
            if rtsp
            else []
        )
        return [*extra, *opts, *live, "-i", src.url]
    assert isinstance(src, FileSource)
    return [*extra, "-re", "-stream_loop", "-1", "-i", src.path]  # dev file: real-time, looping


async def build_args(
    cam: CameraConfig, rec: RecordingConfig, out_path: str | Path, platform: str | None = None
) -> list[str]:
    container = ["-movflags", FRAGMENTED_MP4] if rec.format in ("mp4", "mov", "m4v") else []
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        *await input_args(cam, rec, platform or sys.platform),
        "-an",
        "-vf",
        f"scale={rec.width}:{rec.height},fps={rec.fps}",
        "-c:v",
        rec.video_codec,
        *rec.encoder_args,
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(GOP_SIZE),
        *container,
        "-flush_packets",
        "1",
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(out_path),
    ]


@dataclass
class CameraState:
    id: str
    name: str
    status: str
    frames: int
    file: str | None
    error: str | None


class CameraRecorder:
    def __init__(
        self,
        cam: CameraConfig,
        rec: RecordingConfig,
        out_path: str | Path,
        *,
        spawn_fn: SpawnFn | None = None,
        on_change: Callable[[], None] | None = None,
        stop_timeout: float = STOP_TIMEOUT_S,
        poll_interval: float = 0.5,
    ) -> None:
        self.cam = cam
        self.rec = rec
        self.out_path = Path(out_path)
        self.status = "idle"
        self.error: str | None = None
        self.frames = 0
        self.first_frame_at: float | None = None  # wall-clock time (time.time) of first frame
        self._spawn_fn = spawn_fn
        self._on_change = on_change or (lambda: None)
        self._stop_timeout = stop_timeout
        self._poll = poll_interval
        self._proc: Any = None
        self._stopping = False
        self._last_progress = 0.0
        self._ready: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._exited = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._stderr = ""

    # ---------------- public ----------------
    async def start(self) -> bool:
        """Start ffmpeg. True once frames flow, False if the camera failed. Never raises."""
        self._set_status("connecting")
        try:
            args = await build_args(self.cam, self.rec, self.out_path)
            if self._spawn_fn:
                self._proc = await self._spawn_fn(args, self.cam)
            else:
                self._proc = await default_spawn(self.rec.ffmpeg_path, args)
        except Exception as err:  # noqa: BLE001 - any spawn failure means "camera unavailable"
            self._fail("unavailable", mask(err))
            self._finish_ready(False)
            self._exited.set()
            return False
        self._tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
            asyncio.create_task(self._wait_exit()),
            asyncio.create_task(self._watchdog()),
        ]
        return await self._ready

    async def stop(self) -> None:
        """Graceful stop: ffmpeg finalizes the file on `q`. Force-kills if it hangs."""
        if self._proc is None or self._exited.is_set():
            return
        self._stopping = True
        with contextlib.suppress(Exception):
            self._proc.stdin.write(b"q\n")
            await self._proc.stdin.drain()
        try:
            await asyncio.wait_for(self._exited.wait(), self._stop_timeout)
        except TimeoutError:
            log.warning("[%s] ffmpeg did not stop in time - killing it", self.cam.id)
            self.kill()
            await self._exited.wait()

    def kill(self) -> None:
        """Immediate kill. Synchronous, so it is safe in exit handlers."""
        if self._proc is not None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self._proc.kill()

    def state(self) -> CameraState:
        return CameraState(
            id=self.cam.id,
            name=self.cam.name,
            status=self.status,
            frames=self.frames,
            error=self.error,
            file=self.out_path.name if self.frames > 0 else None,
        )

    # ---------------- internals ----------------
    def _set_status(self, status: str) -> None:
        self.status = status
        self._on_change()

    def _fail(self, status: str, error: str) -> None:
        self.error = error
        log.error("[%s] %s: %s", self.cam.id, self.cam.name, error)
        self._set_status(status)

    def _finish_ready(self, ok: bool) -> None:
        if not self._ready.done():
            self._ready.set_result(ok)

    async def _read_stdout(self) -> None:
        out = self._proc.stdout
        while line := await out.readline():
            m = _FRAME.match(line.decode("utf-8", errors="replace").strip())
            if not m:
                continue
            self.frames = int(m.group(1))
            self._last_progress = time.monotonic()
            if self.status == "connecting" and self.frames > 0:
                self.first_frame_at = time.time()
                log.info("[%s] recording -> %s", self.cam.id, self.out_path.name)
                self._set_status("recording")
                self._finish_ready(True)
            else:
                self._on_change()

    async def _read_stderr(self) -> None:
        err = self._proc.stderr
        while chunk := await err.read(1024):
            self._stderr = (self._stderr + mask(chunk.decode("utf-8", errors="replace")))[-1000:]

    async def _watchdog(self) -> None:
        opened_by = time.monotonic() + self.rec.open_timeout_seconds
        stall = self.rec.disconnect_timeout_seconds
        while not self._exited.is_set():
            await asyncio.sleep(self._poll)
            now = time.monotonic()
            if self.status == "connecting" and now > opened_by:
                self._fail(
                    "unavailable", f"No video received within {self.rec.open_timeout_seconds}s"
                )
                self.kill()
            elif self.status == "recording" and now - self._last_progress > stall:
                self._fail("failed", f"Camera disconnected (no frames for {stall}s)")
                self.kill()

    async def _wait_exit(self) -> None:
        code = await self._proc.wait()
        # Let the readers drain what ffmpeg wrote just before exiting.
        for t in self._tasks[:2]:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(t, 1.0)
        await self._on_close(code)

    async def _on_close(self, code: int | None) -> None:
        tail = " | ".join(self._stderr.strip().splitlines()[-2:])
        detail = f": {tail}" if tail else ""
        if self.status == "connecting":
            self._fail("unavailable", f"Could not open camera{detail}")
        elif self.status == "recording":
            if not self._stopping:
                self._fail("failed", f"Camera stream ended unexpectedly (exit code {code}){detail}")
            elif self.frames > 0:
                self._set_status("completed")
            else:
                self._fail("failed", "No frames were recorded")
        # status == "failed": already marked by the watchdog; keep that message.

        with contextlib.suppress(OSError):
            if self.frames == 0 or (self.out_path.exists() and self.out_path.stat().st_size == 0):
                self.out_path.unlink(missing_ok=True)  # never leave empty/invalid files
        if self.frames > 0:
            log.info("[%s] stopped - %d frames written", self.cam.id, self.frames)
        self._finish_ready(False)
        self._exited.set()
        for t in self._tasks[3:]:
            t.cancel()
