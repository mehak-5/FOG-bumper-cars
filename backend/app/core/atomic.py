"""Crash-safe file replacement: write tmp -> flush -> fsync -> os.replace -> fsync directory.

A reader (or a restart after power loss) sees either the complete old file or the complete new
one, never a truncated mix. Used for session.json, venue.yaml and share_index.json.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any


def _fsync_dir(directory: Path) -> None:
    """Persist the rename itself. Windows cannot open directories, so it is skipped there."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Leave the previous file untouched and do not strand a half-written temp file.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str | Path, obj: Any, *, indent: int | None = 2) -> None:
    # Serialize first so an unserializable object never creates a temp file.
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=False))
