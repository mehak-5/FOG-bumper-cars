import asyncio
import base64
import logging
import time
from typing import Optional
from app.core.config import CameraConfig, RecordingConfig
from app.core.state import CameraReadiness

logger = logging.getLogger("preview")

FRAME_FRESH_MS = 2000


class PreviewPipe:
    """Lightweight FFmpeg process that converts a camera stream into low-fps (~2fps) JPEG frames for operator UI."""

    def __init__(self, cam: CameraConfig, rec: RecordingConfig, ffmpeg_bin: str) -> None:
        self.cam = cam
        self.rec = rec
        self.ffmpeg_bin = ffmpeg_bin
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.last_frame: Optional[bytes] = None
        self.last_frame_at: Optional[float] = None
        self.recording: bool = False
        self.stopped: bool = False
        self.backoff: float = 1.0
        self._retry_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self.stopped or self.recording:
            return

        cmd = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel", "error",
        ]

        source = self.cam.source
        if isinstance(source, int) or str(source).isdigit():
            cmd.extend(["-f", "dshow", "-rtbufsize", "64M", "-i", f"video={source}"])
        elif str(source).startswith("rtsp://") or str(source).startswith("http://"):
            cmd.extend(["-rtsp_transport", "tcp", "-timeout", "5000000", "-i", str(source)])
        else:
            cmd.extend(["-re", "-stream_loop", "-1", "-i", str(source)])

        cmd.extend([
            "-vf", "scale=320:180,fps=2",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-",
        ])

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            asyncio.create_task(self._read_mjpeg())
        except Exception as e:
            logger.warning("[%s] Preview pipe failed to start: %s", self.cam.id, e)
            self._schedule_retry()

    async def stop(self) -> None:
        self.stopped = True
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
        if self.proc:
            try:
                self.proc.kill()
                await self.proc.wait()
            except Exception:
                pass

    def set_recording(self, recording: bool) -> None:
        was_recording = self.recording
        self.recording = recording

        if not was_recording and recording:
            # Kill preview pipe to release camera for recorder
            if self.proc:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        elif was_recording and not recording:
            # Reopen preview pipe after ride
            if not self.stopped:
                asyncio.create_task(self.start())

    def get_frame_data_url(self) -> Optional[str]:
        if not self.last_frame:
            return None
        b64 = base64.b64encode(self.last_frame).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"

    def is_ready(self) -> bool:
        if self.recording:
            return True
        if not self.last_frame_at:
            return False
        return (time.time() - self.last_frame_at) * 1000 < FRAME_FRESH_MS

    def get_readiness(self) -> CameraReadiness:
        if self.recording:
            return CameraReadiness(ready=True, reason="recording", last_frame_at=self.last_frame_at)
        if self.last_frame_at and (time.time() - self.last_frame_at) * 1000 < FRAME_FRESH_MS:
            return CameraReadiness(ready=True, reason="live", last_frame_at=self.last_frame_at)
        if self.last_frame_at:
            return CameraReadiness(ready=False, reason="stale frame", last_frame_at=self.last_frame_at)
        return CameraReadiness(ready=False, reason="no frame yet", last_frame_at=None)

    def _schedule_retry(self) -> None:
        if self.stopped or self.recording:
            return

        async def _retry():
            await asyncio.sleep(self.backoff)
            self.backoff = min(self.backoff * 2, 5.0)  # Capped at 5s backoff
            await self.start()

        self._retry_task = asyncio.create_task(_retry())

    async def _read_mjpeg(self) -> None:
        if not self.proc or not self.proc.stdout:
            return

        buf = bytearray()
        SOI = b"\xff\xd8"
        EOI = b"\xff\xd9"

        while True:
            chunk = await self.proc.stdout.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
            self.backoff = 1.0  # Reset backoff on receiving data

            while True:
                soi_idx = buf.find(SOI)
                if soi_idx == -1:
                    buf.clear()
                    break
                if soi_idx > 0:
                    del buf[:soi_idx]
                eoi_idx = buf.find(EOI, 2)
                if eoi_idx == -1:
                    break
                frame = bytes(buf[: eoi_idx + 2])
                del buf[: eoi_idx + 2]
                self.last_frame = frame
                self.last_frame_at = time.time()

        if not self.stopped and not self.recording:
            self._schedule_retry()


class PreviewManager:
    """Manages low-latency preview streams for all cameras."""

    def __init__(self, cameras: list[CameraConfig], rec: RecordingConfig, ffmpeg_bin: str) -> None:
        self.pipes: dict[str, PreviewPipe] = {
            c.id: PreviewPipe(c, rec, ffmpeg_bin) for c in cameras if c.enabled
        }

    async def start(self) -> None:
        for pipe in self.pipes.values():
            asyncio.create_task(pipe.start())

    async def stop(self) -> None:
        for pipe in self.pipes.values():
            await pipe.stop()

    def set_recording(self, camera_id: str, recording: bool) -> None:
        if camera_id in self.pipes:
            self.pipes[camera_id].set_recording(recording)

    def get_frame(self, camera_id: str) -> Optional[str]:
        pipe = self.pipes.get(camera_id)
        return pipe.get_frame_data_url() if pipe else None

    def is_ready(self, camera_id: str) -> bool:
        pipe = self.pipes.get(camera_id)
        return pipe.is_ready() if pipe else False

    def get_readiness(self) -> dict[str, CameraReadiness]:
        return {cam_id: pipe.get_readiness() for cam_id, pipe in self.pipes.items()}
