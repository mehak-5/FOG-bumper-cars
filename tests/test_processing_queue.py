"""Tests for the background processing worker process and job queue."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus, Events
from backend.app.processing import ProcessingService
from backend.app.services.session_store import read_session

VALID_ID = "20260101_120000_123456"
VALID_ID_2 = "20260101_120001_654321"


def make_session(
    sessions_dir: Path,
    session_id: str = VALID_ID,
    *,
    status: str = "completed",
    processing: dict[str, Any] | None = None,
) -> Path:
    sdir = sessions_dir / f"ride_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": session_id,
        "status": status,
        "start_time": "2026-01-01T12:00:00+00:00",
        "end_time": "2026-01-01T12:05:00+00:00",
        "duration_seconds": 300.0,
        "cameras": [],
    }
    if processing is not None:
        meta["processing"] = processing
    atomic_write_json(sdir / "session.json", meta)
    return sdir


async def test_queued_job_runs_and_reaches_done(tmp_path):
    bus = EventBus()
    progress_events = []
    reel_events = []
    bus.subscribe(Events.PROCESSING_PROGRESS, lambda d: progress_events.append(d))
    bus.subscribe(Events.REEL_READY, lambda d: reel_events.append(d))

    sdir = make_session(tmp_path, VALID_ID, processing={"status": "pending"})
    service = ProcessingService(tmp_path, bus)
    service.start()

    try:
        # Wait up to 5s for job to finish and IPC queue to drain
        for _ in range(50):
            meta = read_session(sdir)
            if (
                meta
                and meta.get("processing", {}).get("status") == "done"
                and any(
                    e.get("status") == "done"
                    for e in progress_events
                    if e["session_id"] == VALID_ID
                )
            ):
                break
            await asyncio.sleep(0.1)

        meta = read_session(sdir)
        assert meta is not None
        proc = meta.get("processing", {})
        assert proc.get("status") == "done"
        assert "completed_at" in proc

        # Progress events should cover extracting -> done
        statuses = [e["status"] for e in progress_events if e["session_id"] == VALID_ID]
        assert "extracting" in statuses
        assert "building" in statuses
        assert "done" in statuses
        assert any(e["session_id"] == VALID_ID for e in reel_events)
    finally:
        await service.stop()


async def test_failed_job_saves_stage_and_stderr(tmp_path):
    bus = EventBus()
    failed_events = []
    bus.subscribe(Events.PROCESSING_FAILED, lambda d: failed_events.append(d))

    sdir = make_session(tmp_path, VALID_ID, processing={"status": "pending"})
    (sdir / "fail_stage.txt").write_text("segmenting", encoding="utf-8")

    service = ProcessingService(tmp_path, bus)
    service.start()

    try:
        for _ in range(50):
            meta = read_session(sdir)
            if meta and meta.get("processing", {}).get("status") == "failed" and failed_events:
                break
            await asyncio.sleep(0.1)

        meta = read_session(sdir)
        assert meta is not None
        proc = meta.get("processing", {})
        assert proc.get("status") == "failed"
        assert proc.get("stage") == "segmenting"
        assert "Simulated pipeline failure" in proc.get("stderr", "")

        assert len(failed_events) >= 1
        assert failed_events[0]["session_id"] == VALID_ID
        assert failed_events[0]["stage"] == "segmenting"
        assert "Simulated pipeline failure" in failed_events[0]["error"]
    finally:
        await service.stop()


async def test_second_job_waits_for_first_to_finish(tmp_path):
    bus = EventBus()
    completed_order = []
    bus.subscribe(
        Events.PROCESSING_PROGRESS,
        lambda d: completed_order.append(d["session_id"]) if d.get("status") == "done" else None,
    )

    sdir1 = make_session(tmp_path, VALID_ID, processing={"status": "pending"})
    sdir2 = make_session(tmp_path, VALID_ID_2, processing={"status": "pending"})

    service = ProcessingService(tmp_path, bus, auto_scan=False)
    service.start()

    try:
        assert service.enqueue(VALID_ID) is True
        assert service.enqueue(VALID_ID_2) is True
        # Enqueueing same ID while pending or running returns False
        assert service.enqueue(VALID_ID) is False

        for _ in range(60):
            m1 = read_session(sdir1)
            m2 = read_session(sdir2)
            if (
                m1
                and m1.get("processing", {}).get("status") == "done"
                and m2
                and m2.get("processing", {}).get("status") == "done"
            ):
                break
            await asyncio.sleep(0.1)

        assert read_session(sdir1)["processing"]["status"] == "done"
        assert read_session(sdir2)["processing"]["status"] == "done"
        assert completed_order == [VALID_ID, VALID_ID_2]
    finally:
        await service.stop()


async def test_ride_finished_auto_queues_processing(tmp_path):
    bus = EventBus()
    queued = []
    bus.subscribe(Events.PROCESSING_QUEUED, lambda d: queued.append(d["session_id"]))

    sdir = make_session(tmp_path, VALID_ID)  # no processing block yet

    service = ProcessingService(tmp_path, bus, auto_scan=False)
    service.start()

    try:
        await bus.publish(Events.RIDE_FINISHED, {"session_id": VALID_ID, "status": "completed"})

        for _ in range(50):
            meta = read_session(sdir)
            if meta and meta.get("processing", {}).get("status") == "done":
                break
            await asyncio.sleep(0.1)

        assert VALID_ID in queued
        assert read_session(sdir)["processing"]["status"] == "done"
    finally:
        await service.stop()


async def test_recording_session_cannot_be_processed(tmp_path):
    bus = EventBus()
    failed = []
    bus.subscribe(Events.PROCESSING_FAILED, lambda d: failed.append(d))

    sdir = make_session(tmp_path, VALID_ID, status="recording")

    service = ProcessingService(tmp_path, bus, auto_scan=False)
    service.start()

    try:
        service.enqueue(VALID_ID)

        for _ in range(50):
            meta = read_session(sdir)
            if meta and meta.get("processing", {}).get("status") == "failed" and failed:
                break
            await asyncio.sleep(0.1)

        meta = read_session(sdir)
        assert meta["processing"]["status"] == "failed"
        assert "still recording" in meta["processing"]["stderr"]
    finally:
        await service.stop()
