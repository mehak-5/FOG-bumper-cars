"""Reads, lists and deletes ride folders. Every session id is validated before touching disk."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

SESSION_ID = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6}$")


class InvalidSessionId(ValueError):
    pass


def session_dir(sessions_dir: Path, session_id: str) -> Path:
    """Folder for a session id. Rejects anything that is not exactly our id format."""
    if not SESSION_ID.fullmatch(session_id):
        raise InvalidSessionId(f"Invalid session id: {session_id!r}")
    path = (sessions_dir / f"ride_{session_id}").resolve()
    if path.parent != sessions_dir.resolve():  # defence in depth against traversal/symlinks
        raise InvalidSessionId(f"Invalid session id: {session_id!r}")
    return path


def read_session(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((path / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_sessions(sessions_dir: Path) -> list[dict[str, Any]]:
    """All readable sessions, newest first. Corrupt or foreign entries are skipped."""
    out: list[dict[str, Any]] = []
    try:
        entries = [d for d in sessions_dir.iterdir() if d.is_dir() and d.name.startswith("ride_")]
    except OSError:
        return []
    for d in entries:
        meta = read_session(d)
        if meta is not None:
            out.append(meta)
    out.sort(key=lambda m: str(m.get("start_time", "")), reverse=True)
    return out


def delete_session(sessions_dir: Path, session_id: str) -> bool:
    path = session_dir(sessions_dir, session_id)
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True
