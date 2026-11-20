"""Startup utilities: crash recovery, stuck-job reset, temp cleanup and disk space check.

Port of src/backend/services/startup.ts, extended per the spec (Stage 1).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus, Events

log = logging.getLogger("startup")

# session.json["processing"]["status"] values that mean "a worker was in the middle of this job".
IN_FLIGHT_STATUSES = frozenset({"extracting", "segmenting", "ranking", "building"})


def _read_session(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # missing or corrupt: skip, never crash startup
    return data if isinstance(data, dict) else None


def _session_files(sessions_dir: Path) -> list[Path]:
    try:
        return sorted(
            d / "session.json"
            for d in sessions_dir.iterdir()
            if d.is_dir() and d.name.startswith("ride_")
        )
    except OSError:
        return []  # sessions_dir may not exist yet on first run


def recover_interrupted_sessions(sessions_dir: Path) -> int:
    """Rewrite session.json files left in status 'recording' as 'interrupted'."""
    recovered = 0
    for f in _session_files(sessions_dir):
        meta = _read_session(f)
        if meta is None or meta.get("status") != "recording":
            continue
        meta.update(status="interrupted", end_time=None, stop_reason="interrupted")
        atomic_write_json(f, meta)
        log.warning("Recovered interrupted session: %s", f.parent.name)
        recovered += 1
    return recovered


def reset_stuck_jobs(sessions_dir: Path) -> int:
    """Processing jobs that were mid-flight when the app died go back to 'pending'."""
    reset = 0
    for f in _session_files(sessions_dir):
        meta = _read_session(f)
        proc = meta.get("processing") if meta else None
        if not isinstance(proc, dict) or proc.get("status") not in IN_FLIGHT_STATUSES:
            continue
        proc["status"] = "pending"
        proc.pop("stage", None)
        atomic_write_json(f, meta)
        log.warning("Reset stuck processing job: %s", f.parent.name)
        reset += 1
    return reset


def clean_tmp_files(root: Path) -> int:
    """Delete leftover *.tmp files from interrupted atomic writes."""
    removed = 0
    if not root.exists():
        return 0
    for f in root.rglob("*.tmp"):
        try:
            if f.is_file():
                f.unlink()
                removed += 1
        except OSError:
            log.warning("Could not remove temp file %s", f)
    return removed


@dataclass
class DiskStatus:
    free_gb: float
    warn: bool


def check_disk(path: Path, warn_gb: float) -> DiskStatus:
    """Free space on the volume containing `path` (or its nearest existing parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free_gb = shutil.disk_usage(probe).free / 1024**3
    except OSError:
        log.warning("disk check failed for %s", path, exc_info=True)
        return DiskStatus(float("inf"), False)
    return DiskStatus(free_gb, free_gb < warn_gb)


@dataclass
class StartupReport:
    interrupted: int
    jobs_reset: int
    tmp_removed: int
    disk: DiskStatus


class StartupService:
    def __init__(self, sessions_dir: Path, warn_gb: float, bus: EventBus | None = None) -> None:
        self.sessions_dir = sessions_dir
        self.warn_gb = warn_gb
        self.bus = bus

    async def run(self) -> StartupReport:
        # tmp files first so a stranded half-written session.json.tmp is never mistaken for data
        report = StartupReport(
            tmp_removed=clean_tmp_files(self.sessions_dir),
            interrupted=recover_interrupted_sessions(self.sessions_dir),
            jobs_reset=reset_stuck_jobs(self.sessions_dir),
            disk=check_disk(self.sessions_dir, self.warn_gb),
        )
        if self.bus:
            if report.interrupted:
                await self._notice("warning", f"Recovered {report.interrupted} interrupted ride(s)")
            if report.disk.warn:
                await self._notice(
                    "warning",
                    f"Low disk space: {report.disk.free_gb:.1f} GB free "
                    f"(warn below {self.warn_gb} GB)",
                )
        log.info(
            "Startup: %d interrupted, %d jobs reset, %d tmp removed, %.1f GB free",
            report.interrupted,
            report.jobs_reset,
            report.tmp_removed,
            report.disk.free_gb,
        )
        return report

    async def _notice(self, level: str, text: str) -> None:
        assert self.bus is not None
        await self.bus.publish(Events.APP_NOTICE, {"level": level, "text": text})
