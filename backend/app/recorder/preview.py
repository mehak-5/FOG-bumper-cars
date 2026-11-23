"""Persistent preview manager: one lightweight FFmpeg JPEG pipe per enabled camera.

Keeps pipes alive between rides and reconnects with exponential backoff. Pipes are closed while
a ride records (DirectShow devices cannot be opened by two readers at once) and reopened after.
Port of src/backend/services/preview.ts.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from backend.app.config import (
    CameraConfig,
    FileSource,
    RecordingConfig,
    StreamSource,
    WebcamSource,
)
from backend.app.recorder.devices import list_webcams

log = logging.getLogger("preview")

FRAME_FRESH_S = 2.0
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

SpawnFn = Callable[[list[str], CameraConfig], Awaitable[Any]]
FrameCb = Callable[[str, bytes], None]


def extract_frames(buf: bytearray) -> list[bytes]:
    """Pull complete JPEGs out of an MJPEG byte stream, leaving any partial frame in `buf`."""
    frames: list[bytes] = []
    while True:
        soi = buf.find(SOI)
        if soi == -1:
            buf.clear()
            break
        if soi > 0:
            del buf[:soi]  # drop junk before the frame start
        eoi = buf.find(EOI, 2)
        if eoi == -1:
            break  # partial frame - wait for more data
        frames.append(bytes(buf[: eoi + 2]))
        del buf[: eoi + 2]
    return frames


async def preview_input_args(cam: CameraConfig, rec: RecordingConfig, platform: str) -> list[str]:
    extra = list(cam.input_args or [])
    src = cam.source
    if isinstance(src, WebcamSource):
        if platform == "win32":
            name = src.name
            if name is None:
                names = await list_webcams(rec.ffmpeg_path)
                idx = src.index or 0
                name = names[idx] if idx < len(names) else None
            if not name:
                raise RuntimeError(f"Webcam #{src.index} not found for preview")
            return [*extra, "-f", "dshow", "-i", f"video={name}"]
        return [*extra, "-f", "v4l2", "-i", src.name or f"/dev/video{src.index}"]
    if isinstance(src, StreamSource):
        rtsp = src.url.lower().startswith("rtsp")
        opts = (
            ["-rtsp_transport", "tcp", "-timeout", str(int(rec.open_timeout_seconds * 1e6))]
            if rtsp
            else []
        )
        return [*extra, *opts, "-i", src.url]
    assert isinstance(src, FileSource)
    return [*extra, "-re", "-stream_loop", "-1", "-i", src.path]


async def preview_args(
    cam: CameraConfig, rec: RecordingConfig, platform: str | None = None
) -> list[str]:
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        *await preview_input_args(cam, rec, platform or sys.platform),
        "-vf",
        "scale=320:180,fps=2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-",
    ]


async def _default_spawn(ffmpeg_path: str, args: list[str]) -> asyncio.subprocess.Process:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return await asyncio.create_subprocess_exec(
        ffmpeg_path,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **kwargs,
    )


@dataclass
class _Pipe:
    cam: CameraConfig
    proc: Any = None
    task: asyncio.Task[None] | None = None
    last_frame: bytes | None = None
    last_frame_at: float | None = None
    recording: bool = False
    active: bool = False
    backoff: float = 1.0
    buf: bytearray = field(default_factory=bytearray)


class PreviewManager:
    def __init__(
        self,
        cameras: list[CameraConfig],
        rec: RecordingConfig,
        *,
        spawn_fn: SpawnFn | None = None,
        on_frame: FrameCb | None = None,
        clock: Callable[[], float] = time.time,
        backoff_start: float = 1.0,
        backoff_max: float = 30.0,
        auto_open: bool = True,
    ) -> None:
        self.cameras = cameras
        self.rec = rec
        self._spawn_fn = spawn_fn
        self._on_frame = on_frame
        self._clock = clock
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._auto_open = auto_open
        self._pipes: dict[str, _Pipe] = {}
        self._stopped = False

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        """Initialize pipes for all cameras. Call once at app startup (inside the event loop)."""
        for cam in self.cameras:
            pipe = _Pipe(cam, active=self._auto_open, backoff=self._backoff_start)
            self._pipes[cam.id] = pipe
            if self._auto_open:
                self._launch(pipe)

    def stop(self) -> None:
        self._stopped = True
        for pipe in self._pipes.values():
            self._close(pipe)

    def open_feed(self, camera_id: str) -> bool:
        """Open live preview pipe for a specific camera on demand."""
        pipe = self._pipes.get(camera_id)
        if pipe is None or pipe.recording:
            return False
        pipe.active = True
        pipe.backoff = self._backoff_start
        self._launch(pipe)
        return True

    def close_feed(self, camera_id: str) -> bool:
        """Close live preview pipe for a specific camera on demand."""
        pipe = self._pipes.get(camera_id)
        if pipe is None:
            return False
        pipe.active = False
        self._close(pipe)
        pipe.last_frame = None
        pipe.last_frame_at = None
        return True

    # ---------------- queries ----------------
    def get_frame(self, camera_id: str) -> bytes | None:
        pipe = self._pipes.get(camera_id)
        return pipe.last_frame if pipe else None

    def get_frame_data_url(self, camera_id: str) -> str | None:
        frame = self.get_frame(camera_id)
        if frame is None:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")

    def _fresh(self, pipe: _Pipe) -> bool:
        return pipe.last_frame_at is not None and self._clock() - pipe.last_frame_at < FRAME_FRESH_S

    def is_ready(self, camera_id: str) -> bool:
        """True if a fresh frame arrived within 2 s, or if recording.
        If pipe is inactive (feed closed), returns True because the device is idle and free to record."""
        pipe = self._pipes.get(camera_id)
        if pipe is None:
            return False
        if pipe.recording or not pipe.active:
            return True
        return self._fresh(pipe)

    def get_readiness(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for cid, pipe in self._pipes.items():
            at = None if pipe.last_frame_at is None else pipe.last_frame_at * 1000
            if pipe.recording:
                ready, reason = True, "recording"
            elif not pipe.active:
                ready, reason = True, "closed"
            elif self._fresh(pipe):
                ready, reason = True, "live"
            elif pipe.last_frame_at is not None:
                ready, reason = False, "stale frame"
            else:
                ready, reason = False, "no frame yet"
            result[cid] = {"ready": ready, "reason": reason, "last_frame_at": at}
        return result

    # ---------------- recorder hand-off ----------------
    def set_recording(self, camera_id: str, recording: bool) -> None:
        """Called by RecorderManager so we never fight the recorder for the camera device."""
        pipe = self._pipes.get(camera_id)
        if pipe is None or pipe.recording == recording:
            return
        pipe.recording = recording
        if recording:
            log.info("[%s] recorder starting - closing preview pipe", camera_id)
            self._close(pipe)
            pipe.active = False
        elif not self._stopped and pipe.active:
            log.info("[%s] recorder released - reopening preview pipe", camera_id)
            pipe.backoff = self._backoff_start
            self._launch(pipe)

    # ---------------- internals ----------------
    def _launch(self, pipe: _Pipe) -> None:
        if self._stopped or pipe.recording:
            return
        pipe.task = asyncio.get_running_loop().create_task(self._supervise(pipe))

    def _close(self, pipe: _Pipe) -> None:
        if pipe.task:
            pipe.task.cancel()
            pipe.task = None
        if pipe.proc is not None:
            with contextlib.suppress(ProcessLookupError, OSError):
                pipe.proc.kill()
            pipe.proc = None

    async def _supervise(self, pipe: _Pipe) -> None:
        """Run the pipe; if it dies, retry with exponential backoff until stopped/recording."""
        cid = pipe.cam.id
        while not self._stopped and not pipe.recording:
            try:
                await self._run_once(pipe)
                log.warning("[%s] preview pipe closed - retry in %.1fs", cid, pipe.backoff)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - any failure just means "retry later"
                log.warning("[%s] preview pipe error: %s - retry in %.1fs", cid, err, pipe.backoff)
            await asyncio.sleep(pipe.backoff)
            pipe.backoff = min(pipe.backoff * 2, self._backoff_max)

    async def _run_once(self, pipe: _Pipe) -> None:
        args = await preview_args(pipe.cam, self.rec)
        log.info("[%s] opening preview pipe", pipe.cam.id)
        if self._spawn_fn:
            proc = await self._spawn_fn(args, pipe.cam)
        else:
            proc = await _default_spawn(self.rec.ffmpeg_path, args)
        pipe.proc = proc
        pipe.buf = bytearray()
        try:
            while chunk := await proc.stdout.read(65536):
                pipe.backoff = self._backoff_start  # data is flowing: reset backoff
                pipe.buf += chunk
                for frame in extract_frames(pipe.buf):
                    pipe.last_frame = frame
                    pipe.last_frame_at = self._clock()
                    if self._on_frame:
                        self._on_frame(pipe.cam.id, frame)
        finally:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            if pipe.proc is proc:
                pipe.proc = None
