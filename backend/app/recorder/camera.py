import asyncio
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from app.core.config import CameraConfig, RecordingConfig
from app.core.state import CameraState, CameraStatus

logger = logging.getLogger("camera")


def describe_source(source: str | int) -> str:
    if isinstance(source, int) or str(source).isdigit():
        return f"webcam #{source}"
    s = str(source)
    if "://" in s:
        # Mask RTSP credentials
        return re.sub(r"//[^@/\s]+@", "//***@", s)
    return s


def build_camera_args(
    cam: CameraConfig,
    rec: RecordingConfig,
    out_path: Path,
    ffmpeg_bin: str,
) -> list[str]:
    """Constructs FFmpeg arguments with crash-safe fragmented MP4 flags and DirectShow / test inputs."""
    args = [ffmpeg_bin, "-hide_banner", "-loglevel", "error"]

    source = cam.source
    extra = cam.input_args or []

    # Input part
    if isinstance(source, int) or str(source).isdigit():
        # Windows DirectShow webcam
        # If integer index, dshow takes camera name or video device
        args.extend(extra)
        # Note: on Windows, DirectShow uses video="<name>"
        args.extend(["-f", "dshow", "-rtbufsize", "256M", "-i", f"video={source}"])
    elif str(source).startswith("rtsp://") or str(source).startswith("http://"):
        args.extend(extra)
        if str(source).startswith("rtsp://"):
            args.extend(["-rtsp_transport", "tcp", "-timeout", str(int(rec.open_timeout_seconds * 1e6))])
        args.extend(["-i", str(source)])
    else:
        # File / synthetic test loop
        args.extend(extra)
        args.extend(["-re", "-stream_loop", "-1", "-i", str(source)])

    # Output encoding part
    args.extend([
        "-an",
        "-vf", f"scale={rec.width}:{rec.height},fps={rec.fps}",
        "-c:v", rec.video_codec,
        *rec.encoder_args,
        "-pix_fmt", "yuv420p",
        "-g", str(rec.fps * 2),
        "-use_wallclock_as_timestamps", "1",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",  # Crash playable
        "-progress", "pipe:1",
        "-nostats",
        "-y",
        str(out_path),
    ])

    return args


class CameraRecorder:
    """Controls a single FFmpeg process recording one camera stream into an MP4 file."""

    def __init__(
        self,
        cam: CameraConfig,
        rec: RecordingConfig,
        out_path: Path,
        ffmpeg_bin: str,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self.cam = cam
        self.rec = rec
        self.out_path = Path(out_path)
        self.ffmpeg_bin = ffmpeg_bin
        self.on_change = on_change or (lambda: None)

        self.status: CameraStatus = CameraStatus.IDLE
        self.frames: int = 0
        self.error: Optional[str] = None
        self.first_frame_ts: Optional[float] = None
        self.offset_sec: float = 0.0

        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stopping: bool = False
        self._done: bool = False
        self._last_progress_ts: float = 0.0
        self._ready_event = asyncio.Event()

    async def start(self) -> bool:
        """Starts FFmpeg process. Resolves True once first frame is received, False on failure."""
        self._set_status(CameraStatus.CONNECTING)
        cmd = build_camera_args(self.cam, self.rec, self.out_path, self.ffmpeg_bin)
        logger.info("[%s] Spawning FFmpeg: %s", self.cam.id, " ".join(cmd[:8]) + " ...")

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            self._fail(CameraStatus.UNAVAILABLE, f"Failed starting FFmpeg: {e}")
            return False

        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

        # Wait up to open_timeout_seconds for the first frame
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=self.rec.open_timeout_seconds)
            return True
        except asyncio.TimeoutError:
            self._fail(CameraStatus.UNAVAILABLE, f"No frames received within {self.rec.open_timeout_seconds}s")
            await self.stop(force=True)
            return False

    async def stop(self, force: bool = False) -> None:
        """Gracefully stops recording by sending 'q' to FFmpeg's stdin."""
        if not self._proc or self._done:
            return

        self._stopping = True
        if force:
            try:
                self._proc.kill()
            except Exception:
                pass
            return

        # Send 'q' for clean finalization
        if self._proc.stdin and not self._proc.stdin.is_closing():
            try:
                self._proc.stdin.write(b"q\n")
                await self._proc.stdin.drain()
            except Exception:
                pass

        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("[%s] FFmpeg did not exit within 5s on 'q' — force killing", self.cam.id)
            try:
                self._proc.kill()
                await self._proc.wait()
            except Exception:
                pass

    def get_state(self) -> CameraState:
        return CameraState(
            id=self.cam.id,
            name=self.cam.name,
            status=self.status,
            frames=self.frames,
            file=self.out_path.name if self.frames > 0 else None,
            error=self.error,
        )

    def _set_status(self, status: CameraStatus) -> None:
        self.status = status
        self.on_change()

    def _fail(self, status: CameraStatus, error_msg: str) -> None:
        self.error = error_msg
        logger.error("[%s] %s: %s", self.cam.id, self.cam.name, error_msg)
        self._set_status(status)

    async def _read_stdout(self) -> None:
        if not self._proc or not self._proc.stdout:
            return

        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text.startswith("frame="):
                try:
                    num = int(text.split("=")[1].strip())
                    self.frames = num
                    self._last_progress_ts = time.time()
                    if self.status == CameraStatus.CONNECTING and self.frames > 0:
                        self.first_frame_ts = time.time()
                        self._set_status(CameraStatus.RECORDING)
                        self._ready_event.set()
                        logger.info("[%s] Recording confirmed -> %s", self.cam.id, self.out_path.name)
                    else:
                        self.on_change()
                except Exception:
                    pass

        await self._on_close()

    async def _read_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            # Suppress normal stderr, keep watch in case of errors

    async def _on_close(self) -> None:
        if self._done:
            return
        self._done = True
        returncode = await self._proc.wait() if self._proc else None

        if self.status == CameraStatus.CONNECTING:
            self._fail(CameraStatus.UNAVAILABLE, "FFmpeg closed before streaming frames")
        elif self.status == CameraStatus.RECORDING:
            if not self._stopping:
                self._fail(CameraStatus.FAILED, f"Camera stream disconnected unexpectedly (code {returncode})")
            elif self.frames > 0:
                self._set_status(CameraStatus.COMPLETED)
            else:
                self._fail(CameraStatus.FAILED, "No frames were recorded")

        # Never keep zero-byte corrupt video files
        if self.frames == 0 and self.out_path.exists():
            try:
                self.out_path.unlink()
            except Exception:
                pass
