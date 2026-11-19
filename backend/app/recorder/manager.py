import asyncio
import logging
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import AppConfig
from app.core.event_bus import EventBus
from app.core.state import AppState, CameraState, CameraStatus, RideStatus, TriggerState
from app.recorder.camera import CameraRecorder
from app.recorder.preview import PreviewManager
from app.storage.session_store import SessionStore

logger = logging.getLogger("recorder_manager")


class RecorderManager:
    """Controls the full ride lifecycle and orchestrates camera recorders."""

    def __init__(
        self,
        cfg: AppConfig,
        bus: EventBus,
        store: SessionStore,
        preview_manager: PreviewManager,
        ffmpeg_bin: str,
    ) -> None:
        self.cfg = cfg
        self.bus = bus
        self.store = store
        self.preview_manager = preview_manager
        self.ffmpeg_bin = ffmpeg_bin

        self.ride: RideStatus = RideStatus.IDLE
        self.session_id: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.ride_ends_at: Optional[float] = None
        self.active_session_dir: Optional[Path] = None
        self.cameras: list[CameraRecorder] = []
        self.last_cameras: list[CameraState] = []
        self.last_session: Optional[dict] = None
        self.trigger_state: Optional[TriggerState] = None
        self.disk_free_gb: Optional[float] = None
        self.notes: str = ""

        self._lock = asyncio.Lock()
        self._auto_stop_task: Optional[asyncio.Task] = None

        # Wire up EventBus subscriptions
        self.bus.subscribe("ride.start", self._on_ride_start)
        self.bus.subscribe("ride.end", self._on_ride_end)

    def get_state(self) -> AppState:
        cameras_state = (
            [c.get_state() for c in self.cameras]
            if self.cameras
            else (
                self.last_cameras
                if self.last_cameras
                else [
                    CameraState(id=c.id, name=c.name, status=CameraStatus.IDLE)
                    for c in self.cfg.cameras
                    if c.enabled
                ]
            )
        )

        return AppState(
            venue=self.cfg.venue.name,
            ride=self.ride,
            session_id=self.session_id,
            started_at=self.started_at.isoformat() if self.started_at else None,
            cameras=cameras_state,
            last_session=self.last_session,
            readiness=self.preview_manager.get_readiness(),
            trigger=self.trigger_state,
            ride_ends_at=self.ride_ends_at,
            disk_free_gb=self.disk_free_gb,
            notes=self.notes,
        )

    def update_trigger_state(self, ts: TriggerState) -> None:
        self.trigger_state = ts
        asyncio.create_task(self.notify())

    def update_disk_status(self, free_gb: float) -> None:
        self.disk_free_gb = free_gb
        asyncio.create_task(self.notify())

    def set_notes(self, notes: str) -> None:
        self.notes = notes

    async def notify(self) -> None:
        await self.bus.publish("state.changed", self.get_state().model_dump())

    async def _on_ride_start(self, _event: str, payload: dict) -> None:
        source = payload.get("source", "unknown")
        async with self._lock:
            if self.ride != RideStatus.IDLE:
                logger.warning("Ride start ignored — ride currently in state '%s'", self.ride.value)
                return

            # Check camera readiness guard
            enabled_cams = [c for c in self.cfg.cameras if c.enabled]
            not_ready = [c for c in enabled_cams if not self.preview_manager.is_ready(c.id)]
            if not_ready:
                reasons = [f"{c.name}: not ready" for c in not_ready]
                msg = f"Cannot start ride — cameras not ready ({', '.join(reasons)})"
                logger.error(msg)
                await self.bus.publish("app.notice", {"level": "error", "text": msg})
                return

            self.ride = RideStatus.STARTING
            await self.notify()

            now = datetime.now()
            stamp = now.strftime("%Y%m%d_%H%M%S")
            hex_token = secrets.token_hex(3)
            self.session_id = f"{stamp}_{hex_token}"
            self.started_at = now
            self.notes = ""

            # Create ride directory atomically
            self.active_session_dir = self.store.create_session_dir(self.session_id)

            # Setup recorders
            self.cameras = [
                CameraRecorder(
                    cam=c,
                    rec=self.cfg.recording,
                    out_path=self.active_session_dir / f"{c.id}.{self.cfg.recording.container}",
                    ffmpeg_bin=self.ffmpeg_bin,
                    on_change=lambda: asyncio.create_task(self.notify()),
                )
                for c in enabled_cams
            ]

            # Pause preview pipes
            for c in enabled_cams:
                self.preview_manager.set_recording(c.id, True)

            # Launch all camera recorders in parallel
            results = await asyncio.gather(*[c.start() for c in self.cameras])
            live_cams = [c for c, ok in zip(self.cameras, results) if ok]

            if not live_cams:
                logger.error("No cameras could be started — ride aborted")
                await self.bus.publish("app.notice", {"level": "error", "text": "No cameras could be started — ride aborted"})
                await self._finalize(reason="start_failed")
                return

            # Calculate camera timeline offsets
            base_time = min(c.first_frame_ts or time.time() for c in live_cams)
            for c in live_cams:
                c.offset_sec = (c.first_frame_ts or base_time) - base_time

            self.ride = RideStatus.RECORDING
            limits = [
                val for val in [self.cfg.recording.ride_duration_seconds, self.cfg.recording.hard_cap_seconds]
                if val and val > 0
            ]
            if limits:
                duration_limit = min(limits)
                self.ride_ends_at = time.time() + duration_limit
                self._auto_stop_task = asyncio.create_task(self._auto_stop_timer(duration_limit, self.session_id))
            else:
                self.ride_ends_at = None

            # Initial atomic session.json write
            await self._save_session_meta(status="recording")
            await self.notify()
            await self.bus.publish("ride.started", {"session_id": self.session_id})
            logger.info("Ride '%s' started by %s (%d/%d cameras active)", self.session_id, source, len(live_cams), len(self.cameras))

    async def _on_ride_end(self, _event: str, payload: dict) -> None:
        reason = payload.get("reason", "trigger")
        async with self._lock:
            if self.ride != RideStatus.RECORDING:
                logger.info("Ride end ignored — no active recording")
                return
            await self._finalize(reason=reason)

    async def _auto_stop_timer(self, delay: float, session_id: str) -> None:
        await asyncio.sleep(delay)
        if self.ride == RideStatus.RECORDING and self.session_id == session_id:
            logger.info("Ride duration reached limit — auto-ending ride")
            await self.bus.publish("ride.end", {"reason": "time_limit"})

    async def _finalize(self, reason: str) -> None:
        if self._auto_stop_task and not self._auto_stop_task.done():
            self._auto_stop_task.cancel()

        self.ride = RideStatus.STOPPING
        await self.notify()

        # Stop camera recordings in parallel
        await asyncio.gather(*[c.stop() for c in self.cameras])

        # Restore preview pipes
        for c in self.cfg.cameras:
            if c.enabled:
                self.preview_manager.set_recording(c.id, False)

        completed_count = sum(1 for c in self.cameras if c.status == CameraStatus.COMPLETED)
        status = (
            "completed"
            if completed_count == len(self.cameras)
            else ("partial" if completed_count > 0 else "failed")
        )

        await self._save_session_meta(status=status, stop_reason=reason)
        self.last_cameras = [c.get_state() for c in self.cameras]
        self.last_session = {"id": self.session_id, "status": status}

        session_finished_id = self.session_id
        self.session_id = None
        self.started_at = None
        self.ride_ends_at = None
        self.cameras = []
        self.ride = RideStatus.IDLE

        await self.notify()
        await self.bus.publish("ride.finished", {"session_id": session_finished_id, "status": status})
        logger.info("Ride '%s' finalized with status '%s' (reason: %s)", session_finished_id, status, reason)

    async def _save_session_meta(self, status: str, stop_reason: Optional[str] = None) -> None:
        if not self.active_session_dir or not self.session_id:
            return

        end_time = datetime.now() if status != "recording" else None
        duration = (
            round((end_time - self.started_at).total_seconds(), 2)
            if (end_time and self.started_at)
            else None
        )

        meta = {
            "session_id": self.session_id,
            "status": status,
            "start_time": self.started_at.isoformat() if self.started_at else None,
            "end_time": end_time.isoformat() if end_time else None,
            "duration_seconds": duration,
            "stop_reason": stop_reason,
            "notes": self.notes,
            "cameras": [
                {
                    **c.get_state().model_dump(),
                    "offset_sec": round(c.offset_sec, 3),
                }
                for c in self.cameras
            ],
        }
        self.store.write_metadata(self.active_session_dir, meta)
