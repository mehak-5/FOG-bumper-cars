"""Processing service and job queue.

Runs processing jobs in a separate OS process, one at a time, so recording
is never interrupted or frame-dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus, Events
from backend.app.processing.worker import run_job
from backend.app.services.session_store import read_session, session_dir

log = logging.getLogger("processing.service")

SpawnFn = Callable[[Path, str, Any], Any]


class ProcessingService:
    def __init__(
        self,
        sessions_dir: Path,
        bus: EventBus,
        *,
        spawn_fn: SpawnFn | None = None,
        auto_scan: bool = True,
    ) -> None:
        self.sessions_dir = sessions_dir
        self.bus = bus
        self._spawn_fn = spawn_fn
        self._auto_scan = auto_scan
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued_ids: set[str] = set()
        self._current_id: str | None = None
        self._current_proc: Any = None
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._unsubscribes: list[Callable[[], None]] = []

    def start(self) -> None:
        """Start the background dispatcher loop and wire EventBus events."""
        if self._dispatcher_task is not None:
            return
        self._stopped = False
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())

        self._unsubscribes = [
            self.bus.subscribe(Events.PROCESSING_QUEUED, self._on_processing_queued),
            self.bus.subscribe(Events.RIDE_FINISHED, self._on_ride_finished),
        ]

        if self._auto_scan:
            self.scan_pending()

    async def stop(self) -> None:
        """Stop the dispatcher and terminate any active worker process."""
        self._stopped = True
        for unsub in self._unsubscribes:
            unsub()
        self._unsubscribes.clear()

        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher_task
            self._dispatcher_task = None

        if self._current_proc is not None:
            with contextlib.suppress(Exception):
                if hasattr(self._current_proc, "terminate"):
                    self._current_proc.terminate()
                if hasattr(self._current_proc, "join"):
                    await asyncio.to_thread(self._current_proc.join, 2.0)
            self._current_proc = None

    def scan_pending(self) -> list[str]:
        """Scan sessions_dir for sessions with processing.status == 'pending' and enqueue them."""
        found: list[str] = []
        try:
            for d in self.sessions_dir.iterdir():
                if d.is_dir() and d.name.startswith("ride_"):
                    meta = read_session(d)
                    if (
                        meta
                        and isinstance(meta.get("processing"), dict)
                        and meta["processing"].get("status") == "pending"
                    ):
                        sid = meta.get("session_id")
                        if sid:
                            self.enqueue(sid)
                            found.append(sid)
        except OSError:
            pass
        return found

    def enqueue(self, session_id: str) -> bool:
        """Enqueue a session for processing. Returns False if already queued or running."""
        if session_id == self._current_id or session_id in self._queued_ids:
            log.info("Session %s is already running or queued", session_id)
            return False
        self._queued_ids.add(session_id)
        self._queue.put_nowait(session_id)
        log.info(
            "Enqueued session %s for processing (queue size: %d)",
            session_id,
            self._queue.qsize(),
        )
        return True

    async def _on_processing_queued(self, data: dict[str, Any]) -> None:
        sid = data.get("session_id")
        if sid:
            self.enqueue(sid)

    async def _on_ride_finished(self, data: dict[str, Any]) -> None:
        sid = data.get("session_id")
        status = data.get("status")
        if sid and status in ("completed", "partial"):
            try:
                sdir = session_dir(self.sessions_dir, sid)
                meta = read_session(sdir)
                if meta:
                    proc = meta.get("processing")
                    if not isinstance(proc, dict):
                        meta["processing"] = {"status": "pending"}
                        atomic_write_json(sdir / "session.json", meta)
                await self.bus.publish(Events.PROCESSING_QUEUED, {"session_id": sid})
            except Exception as err:
                log.error("Failed to prepare ride %s for processing: %s", sid, err)

    async def _dispatcher_loop(self) -> None:
        """Processes one session at a time in FIFO order."""
        while not self._stopped:
            try:
                session_id = await self._queue.get()
            except asyncio.CancelledError:
                break

            self._queued_ids.discard(session_id)
            self._current_id = session_id
            try:
                await self._process_session(session_id)
            except Exception:
                log.exception("Unexpected error processing session %s", session_id)
            finally:
                self._current_id = None
                self._queue.task_done()

    async def _process_session(self, session_id: str) -> None:
        log.info("Starting processing job for session %s", session_id)
        mp_ctx = multiprocessing.get_context("spawn")
        comm_q: multiprocessing.Queue[dict[str, Any]] = mp_ctx.Queue()

        if self._spawn_fn:
            proc = self._spawn_fn(self.sessions_dir, session_id, comm_q)
        else:
            proc = mp_ctx.Process(
                target=run_job,
                args=(str(self.sessions_dir), session_id, comm_q),
                daemon=True,
            )
            proc.start()

        self._current_proc = proc

        # Read messages until process terminates and queue is drained
        while True:
            # Poll queue
            try:
                msg = await asyncio.to_thread(comm_q.get, True, 0.1)
                await self._handle_msg(session_id, msg)
                if msg.get("type") in ("done", "failed"):
                    break
            except queue.Empty:
                is_alive = proc.is_alive() if hasattr(proc, "is_alive") else False
                if not is_alive and comm_q.empty():
                    break
                await asyncio.sleep(0.02)
            except Exception as err:
                log.debug("Queue poll exception: %s", err)
                break

        # Ensure process finishes
        if hasattr(proc, "join"):
            await asyncio.to_thread(proc.join, 5.0)

        self._current_proc = None
        with contextlib.suppress(Exception):
            comm_q.close()

    async def _handle_msg(self, session_id: str, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "progress":
            await self.bus.publish(
                Events.PROCESSING_PROGRESS,
                {
                    "session_id": session_id,
                    "status": msg.get("status", "progress"),
                    "percent": msg.get("percent"),
                },
            )
        elif mtype == "done":
            await self.bus.publish(
                Events.PROCESSING_PROGRESS,
                {
                    "session_id": session_id,
                    "status": "done",
                    "percent": 100.0,
                },
            )
            await self.bus.publish(Events.REEL_READY, {"session_id": session_id})
        elif mtype == "failed":
            await self.bus.publish(
                Events.PROCESSING_FAILED,
                {
                    "session_id": session_id,
                    "stage": msg.get("stage"),
                    "error": msg.get("error"),
                    "stderr": msg.get("stderr"),
                },
            )
            await self.bus.publish(
                Events.PROCESSING_PROGRESS,
                {
                    "session_id": session_id,
                    "status": "failed",
                    "percent": None,
                },
            )
