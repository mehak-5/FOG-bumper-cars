"""Owns the ride/session lifecycle and the per-camera recorders.

Port of src/backend/managers/recorderManager.ts. State machine:
IDLE -> STARTING -> RECORDING -> STOPPING -> IDLE. Start/end are serialized by a lock so they
can never overlap.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from backend.app.config import AppConfig
from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus, Events
from backend.app.recorder.camera import CameraRecorder, SpawnFn, describe_source

log = logging.getLogger("recorder")

IDLE, STARTING, RECORDING, STOPPING = "idle", "starting", "recording", "stopping"


class PreviewLike(Protocol):
    def get_readiness(self) -> dict[str, dict[str, Any]]: ...
    def is_ready(self, camera_id: str) -> bool: ...
    def set_recording(self, camera_id: str, recording: bool) -> None: ...


@dataclass
class Session:
    id: str
    dir: Path
    cameras: list[CameraRecorder]
    started_at: datetime
    started_wall: float
    timer: asyncio.Task[None] | None = None
    lost: set[str] = field(default_factory=set)


def _now() -> datetime:
    return datetime.now(UTC)


def make_session_id(now: datetime | None = None) -> str:
    return f"{(now or datetime.now()):%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"


def write_metadata(s: Session, status: str, reason: str | None = None, notes: str = "") -> None:
    end = None if status == RECORDING else _now()
    cameras = []
    for c in s.cameras:
        d = asdict(c.state())
        # Seconds between ride start and this camera's first frame: aligns cameras on one timeline.
        d["offset_sec"] = (
            round(c.first_frame_at - s.started_wall, 3) if c.first_frame_at is not None else None
        )
        cameras.append(d)
    atomic_write_json(
        s.dir / "session.json",
        {
            "session_id": s.id,
            "status": status,
            "start_time": s.started_at.isoformat(),
            "end_time": end.isoformat() if end else None,
            "duration_seconds": round((end - s.started_at).total_seconds(), 2) if end else None,
            "stop_reason": reason,
            "notes": notes,
            "cameras": cameras,
        },
    )


class RecorderManager:
    def __init__(
        self,
        cfg: AppConfig,
        bus: EventBus,
        *,
        spawn_fn: SpawnFn | None = None,
        fatal: str | None = None,
        preview: PreviewLike | None = None,
        camera_opts: dict[str, Any] | None = None,
        on_frame: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.bus = bus
        self._spawn_fn = spawn_fn
        self._fatal = fatal
        self._preview = preview
        self._camera_opts = camera_opts or {}
        self._on_frame = on_frame
        self.session: Session | None = None
        self.ride = IDLE
        self.ride_ends_at: float | None = None
        self.last_session: dict[str, str] | None = None
        self.trigger_state: dict[str, Any] | None = None
        self.disk_free_gb: float | None = None
        self.notes = ""
        self._last_cameras: list[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        bus.subscribe(Events.RIDE_START, lambda _d: self.start_ride())
        bus.subscribe(Events.RIDE_END, self.end_ride)
        for c in cfg.cameras:
            log.info("Camera %s (%s) <- %s", c.id, c.name, describe_source(c.source))

    # ---------------- state ----------------
    def get_state(self) -> dict[str, Any]:
        s = self.session
        if s:
            cameras = [asdict(c.state()) for c in s.cameras]
        else:
            readiness = self._preview.get_readiness() if self._preview else {}
            cameras = [
                {
                    "id": c.id,
                    "name": c.name,
                    "status": "idle",
                    "frames": 0,
                    "file": None,
                    "error": None if readiness.get(c.id, {}).get("ready") else readiness.get(c.id, {}).get("reason"),
                }
                for c in self.cfg.cameras
            ]
        return {
            "venue": self.cfg.venue.name or "Venue",
            "ride": self.ride,
            "session_id": s.id if s else None,
            "started_at": s.started_at.isoformat() if s else None,
            "cameras": cameras,
            "last_session": self.last_session,
            "readiness": self._preview.get_readiness() if self._preview else {},
            "trigger": self.trigger_state,
            "ride_ends_at": self.ride_ends_at,
            "disk_free_gb": self.disk_free_gb,
            "notes": self.notes,
        }

    def set_fatal(self, message: str | None) -> None:
        """A startup problem (e.g. ffmpeg missing) that makes every ride start fail loudly."""
        self._fatal = message

    def update_trigger_state(self, ts: dict[str, Any]) -> None:
        self.trigger_state = ts
        self._notify()

    def update_disk_status(self, free_gb: float) -> None:
        self.disk_free_gb = free_gb
        self._notify()

    def set_notes(self, notes: str) -> None:
        self.notes = notes

    def _spawn_task(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _notify(self) -> None:
        with contextlib.suppress(RuntimeError):  # no running loop during interpreter teardown
            self._spawn_task(self.bus.publish(Events.STATE_CHANGED, self.get_state()))

    async def _ride_failed_without_session(self) -> None:
        await self.bus.publish(Events.RIDE_FINISHED, {"session_id": None, "status": "failed"})

    async def _notice(self, level: str, text: str) -> None:
        await self.bus.publish(Events.APP_NOTICE, {"level": level, "text": text})

    # ---------------- commands ----------------
    async def start_ride(self) -> None:
        async with self._lock:
            try:
                await self._start()
            except Exception as err:
                log.exception("Could not start ride")
                await self._notice("error", f"Could not start ride: {err}")
                if self.session:
                    await self._finalize("start_error")
                else:
                    await self.bus.publish(
                        Events.RIDE_FINISHED, {"session_id": None, "status": "failed"}
                    )

    async def end_ride(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        reason = data.get("reason", "trigger")
        session_id = data.get("session_id")
        async with self._lock:
            if self.session is None or (session_id and session_id != self.session.id):
                log.info("No active ride - end request ignored")
                return
            await self._finalize(reason)

    async def shutdown(self) -> None:
        await self.end_ride({"reason": "shutdown"})

    def kill_all(self) -> None:
        """Synchronous last resort for exit handlers: kill every running ffmpeg."""
        if self.session:
            for c in self.session.cameras:
                c.kill()

    # ---------------- internals ----------------
    def _limit_seconds(self) -> float | None:
        rec = self.cfg.recording
        limits = [x for x in (rec.ride_duration_seconds, rec.max_duration_seconds) if x]
        return min(limits) if limits else None

    def _on_camera_change(self, session: Session, cam: CameraRecorder) -> None:
        self._notify()
        if self.ride == RECORDING and cam.status == "failed" and cam.cam.id not in session.lost:
            session.lost.add(cam.cam.id)
            self._spawn_task(self._camera_lost(session, cam))

    async def _camera_lost(self, session: Session, cam: CameraRecorder) -> None:
        await self.bus.publish(Events.CAMERA_LOST, {"camera_id": cam.cam.id, "error": cam.error})
        await self._notice("warning", f"Camera {cam.cam.name} disconnected")
        if all(c.status != "recording" for c in session.cameras):
            await self.end_ride({"reason": "all_cameras_lost", "session_id": session.id})

    async def _start(self) -> None:
        if self.session:
            log.warning("Ride %s is already active - start ignored", self.session.id)
            return
        if self._fatal:
            raise RuntimeError(self._fatal)

        # --- readiness guard ---
        preview = self._preview
        if preview:
            not_ready = [c for c in self.cfg.cameras if not preview.is_ready(c.id)]
            if not_ready:
                readiness = preview.get_readiness()
                details = ", ".join(
                    f"{c.name} ({readiness.get(c.id, {}).get('reason', 'unknown')})"
                    for c in not_ready
                )
                msg = f"Camera(s) not ready: {details}"
                log.error(msg)
                await self._notice("error", msg)
                await self.bus.publish(
                    Events.RIDE_FINISHED, {"session_id": None, "status": "failed"}
                )
                return

        rec = self.cfg.recording
        started_at = datetime.now(UTC)
        sid = make_session_id()
        sdir = self.cfg.sessions_dir / f"ride_{sid}"
        sdir.parent.mkdir(parents=True, exist_ok=True)
        sdir.mkdir()  # fails loudly if it exists: rides never share a folder
        self.notes = ""

        cameras = [
            CameraRecorder(
                c,
                rec,
                sdir / f"{c.id}.{rec.format}",
                spawn_fn=self._spawn_fn,
                on_change=lambda: None,
                on_frame=self._on_frame,
                **self._camera_opts,
            )
            for c in self.cfg.cameras
        ]
        session = Session(sid, sdir, cameras, started_at, time.time())
        for cam in cameras:
            cam._on_change = lambda cam=cam: self._on_camera_change(session, cam)
        self.session = session
        self.ride = STARTING
        self._last_cameras = None

        limit = self._limit_seconds()
        self.ride_ends_at = (time.time() + limit) * 1000 if limit else None
        self._notify()

        if preview:
            for c in self.cfg.cameras:
                preview.set_recording(c.id, True)

        await asyncio.gather(*(c.start() for c in cameras))  # all cameras connect in parallel

        live = [c.cam.id for c in cameras if c.status == "recording"]
        log.info("Ride %s started - %d/%d cameras recording", sid, len(live), len(cameras))
        if not live:
            await self._notice("error", "No camera could be started - ride aborted")
            await self._finalize("no_camera_available")
            return
        if len(live) < len(cameras):
            await self._notice("warning", f"Only {len(live)}/{len(cameras)} cameras are recording")

        if limit:
            session.timer = asyncio.create_task(self._time_limit(session, limit))
        self.ride = RECORDING
        write_metadata(session, RECORDING)  # leaves a trace even after a hard crash
        self._notify()
        await self.bus.publish(Events.RIDE_STARTED, {"session_id": sid})

    async def _time_limit(self, session: Session, seconds: float) -> None:
        await asyncio.sleep(seconds)
        session.timer = None  # so _finalize does not cancel the task that is running it
        # session_id guards against ending a *later* ride by mistake
        await self.end_ride({"reason": "time_limit", "session_id": session.id})

    async def _finalize(self, reason: str) -> None:
        s = self.session
        assert s is not None
        if s.timer:
            s.timer.cancel()
            s.timer = None
        self.ride = STOPPING
        self._notify()
        await asyncio.gather(*(c.stop() for c in s.cameras))  # all stop together

        # Release cooldown: allow Windows DirectShow and USB drivers to cleanly release the camera handles
        await asyncio.sleep(1.0)

        if self._preview:
            for c in self.cfg.cameras:
                self._preview.set_recording(c.id, False)

        done = sum(1 for c in s.cameras if c.status == "completed")
        status = "completed" if done == len(s.cameras) else ("partial" if done else "failed")
        try:
            write_metadata(s, status, reason, self.notes)
        except OSError:
            log.exception("Could not write session.json for %s", s.id)
        self._last_cameras = [asdict(c.state()) for c in s.cameras]
        self.last_session = {"id": s.id, "status": status}
        self.session = None
        self.ride = IDLE
        self.ride_ends_at = None
        self._notify()
        log.info("Ride %s finished: %s (%s)", s.id, status, reason)
        if status == "completed":
            await self._notice("info", "Session ended")
        elif status == "partial":
            await self._notice("warning", "Session ended (some cameras had errors)")
        else:
            await self._notice("error", f"Session ended ({reason})")
        await self.bus.publish(Events.RIDE_FINISHED, {"session_id": s.id, "status": status})
