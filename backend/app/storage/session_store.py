import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("storage")


def write_json_atomic(path: Path, data: Any) -> None:
    """Atomic write: writes to .tmp, flushes, fsyncs, then os.replace onto target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    
    os.replace(tmp_path, path)


class SessionStore:
    """Manages ride session folders and metadata."""

    def __init__(self, sessions_dir: str = "storage/sessions") -> None:
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create_session_dir(self, session_id: str) -> Path:
        session_path = self.sessions_dir / f"ride_{session_id}"
        session_path.mkdir(parents=True, exist_ok=False)
        return session_path

    def write_metadata(self, session_dir: Path, data: dict[str, Any]) -> None:
        write_json_atomic(session_dir / "session.json", data)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        if not self.sessions_dir.exists():
            return sessions
        
        for item in sorted(self.sessions_dir.iterdir(), reverse=True):
            if item.is_dir() and item.name.startswith("ride_"):
                meta_file = item / "session.json"
                if meta_file.is_file():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            sessions.append(json.load(f))
                    except Exception:
                        logger.warning("Corrupt session metadata at: %s", meta_file)
        return sessions

    def delete_session(self, session_id: str) -> bool:
        target = self.sessions_dir / f"ride_{session_id}"
        if target.exists() and target.is_dir():
            shutil.rmtree(target)
            return True
        return False


def recover_interrupted_sessions(sessions_dir: Path) -> int:
    """Detects sessions left in 'recording' state (e.g. from crash or power loss) and marks them 'interrupted'."""
    recovered = 0
    if not sessions_dir.exists():
        return recovered

    for item in sessions_dir.iterdir():
        if item.is_dir() and item.name.startswith("ride_"):
            meta_file = item / "session.json"
            if meta_file.is_file():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    
                    if meta.get("status") == "recording":
                        meta["status"] = "interrupted"
                        meta["stop_reason"] = "crash_recovery"
                        write_json_atomic(meta_file, meta)
                        recovered += 1
                        logger.info("Marked crashed session %s as 'interrupted'", item.name)
                except Exception:
                    logger.exception("Failed inspecting session %s for crash recovery", item.name)
    return recovered


def check_disk_space(path: Path, warn_gb: float = 5.0) -> tuple[float, bool]:
    """Returns (free_gb, is_low_space_warning)."""
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        return free_gb, free_gb < warn_gb
    except Exception:
        logger.exception("Failed reading disk usage for %s", path)
        return 0.0, True
