"""Test doubles for ffmpeg processes. Shared by recorder-level tests."""

from __future__ import annotations

import asyncio
from pathlib import Path


class FakeStdin:
    def __init__(self, proc: FakeProc) -> None:
        self.proc = proc
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)
        if data == b"q\n" and self.proc.exit_on_q:
            self.proc.finish(0)

    async def drain(self) -> None:
        pass


class FakeProc:
    """Stands in for asyncio.subprocess.Process."""

    def __init__(self, out_path: Path, exit_on_q: bool = True) -> None:
        self.out_path = out_path
        self.exit_on_q = exit_on_q
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = FakeStdin(self)
        self.returncode: int | None = None
        self._done = asyncio.Event()
        self.killed = False

    def progress(self, frames: int) -> None:
        self.stdout.feed_data(f"frame={frames}\nprogress=continue\n".encode())

    def finish(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self._done.set()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)


class Spawner:
    """spawn_fn that behaves per camera id: ok | silent | raise | stall (frames, then stops)."""

    def __init__(self, behaviors: dict[str, str] | None = None) -> None:
        self.behaviors = behaviors or {}
        self.procs: dict[str, FakeProc] = {}
        self._tasks: list[asyncio.Task[None]] = []

    async def __call__(self, args: list[str], cam) -> FakeProc:
        mode = self.behaviors.get(cam.id, "ok")
        if mode == "raise":
            raise FileNotFoundError("ffmpeg not found")
        out = Path(args[-1])
        proc = FakeProc(out)
        self.procs[cam.id] = proc
        if mode in ("ok", "stall"):
            out.write_bytes(b"fragments")
            asyncio.get_running_loop().call_later(0.02, proc.progress, 3)
        if mode == "ok":
            self._tasks.append(asyncio.create_task(self._pump(proc)))
        return proc

    @staticmethod
    async def _pump(proc: FakeProc) -> None:
        """A healthy camera keeps delivering frames until the process ends."""
        n = 3
        while proc.returncode is None:
            await asyncio.sleep(0.05)
            n += 3
            if proc.returncode is None:
                proc.progress(n)


class ReadyPreview:
    """Preview stub: every camera always has a fresh frame."""

    def __init__(self, camera_ids: list[str]) -> None:
        self.camera_ids = camera_ids
        self.recording: dict[str, bool] = {}

    def get_readiness(self):
        return {c: {"ready": True, "reason": "live", "last_frame_at": 0.0} for c in self.camera_ids}

    def is_ready(self, camera_id: str) -> bool:
        return True

    def set_recording(self, camera_id: str, recording: bool) -> None:
        self.recording[camera_id] = recording
