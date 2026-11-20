"""Worker process target: runs pipeline stages one by one in a separate OS process.

Never block or drop frames from recording: this process runs isolated from the operator app.
"""

from __future__ import annotations

import logging
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json
from backend.app.processing.pipeline import STAGE_PERCENT, STAGES, execute_stage
from backend.app.services.session_store import read_session, session_dir

log = logging.getLogger("processing.worker")


def _put_message(comm_queue: Any, msg: dict[str, Any]) -> None:
    if comm_queue is None:
        return
    try:
        comm_queue.put(msg)
    except Exception:
        log.exception("Failed to put message on communication queue")


def run_job(sessions_dir: str | Path, session_id: str, comm_queue: Any = None) -> None:
    """Entry point for the separate processing worker process.

    Must be pure sync and safe to run in a spawned process on Windows.
    """
    sessions_path = Path(sessions_dir)
    try:
        sdir = session_dir(sessions_path, session_id)
    except Exception as err:
        log.error("Invalid session directory for %s: %s", session_id, err)
        _put_message(
            comm_queue,
            {
                "type": "failed",
                "session_id": session_id,
                "stage": "init",
                "error": str(err),
                "stderr": str(err),
            },
        )
        return

    meta = read_session(sdir)
    if meta is None:
        err_msg = f"session.json missing or unreadable in {sdir}"
        log.error(err_msg)
        _put_message(
            comm_queue,
            {
                "type": "failed",
                "session_id": session_id,
                "stage": "init",
                "error": err_msg,
                "stderr": err_msg,
            },
        )
        return

    if meta.get("status") == "recording":
        err_msg = f"Cannot process session {session_id}: ride is still recording"
        log.error(err_msg)
        meta["processing"] = {
            "status": "failed",
            "stage": "init",
            "stderr": err_msg,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        atomic_write_json(sdir / "session.json", meta)
        _put_message(
            comm_queue,
            {
                "type": "failed",
                "session_id": session_id,
                "stage": "init",
                "error": err_msg,
                "stderr": err_msg,
            },
        )
        return

    current_stage = "init"
    try:
        for stage in STAGES:
            current_stage = stage
            meta = read_session(sdir) or meta
            proc_block = meta.get("processing")
            if not isinstance(proc_block, dict):
                proc_block = {}
            proc_block.update(
                status=stage,
                stage=stage,
                updated_at=datetime.now(UTC).isoformat(),
            )
            proc_block.pop("stderr", None)
            meta["processing"] = proc_block
            atomic_write_json(sdir / "session.json", meta)

            _put_message(
                comm_queue,
                {
                    "type": "progress",
                    "session_id": session_id,
                    "status": stage,
                    "stage": stage,
                    "percent": STAGE_PERCENT.get(stage, 0.0),
                },
            )

            execute_stage(stage, sdir, meta)

        # All stages completed successfully
        meta = read_session(sdir) or meta
        proc_block = meta.get("processing")
        if not isinstance(proc_block, dict):
            proc_block = {}
        proc_block.update(
            status="done",
            completed_at=datetime.now(UTC).isoformat(),
        )
        proc_block.pop("stage", None)
        proc_block.pop("stderr", None)
        meta["processing"] = proc_block
        atomic_write_json(sdir / "session.json", meta)

        _put_message(
            comm_queue,
            {
                "type": "done",
                "session_id": session_id,
                "status": "done",
                "percent": 100.0,
            },
        )
    except Exception as err:
        tb = traceback.format_exc()[-2000:]
        log.exception("Processing failed at stage %s for session %s", current_stage, session_id)
        meta = read_session(sdir) or meta
        proc_block = meta.get("processing")
        if not isinstance(proc_block, dict):
            proc_block = {}
        proc_block.update(
            status="failed",
            stage=current_stage,
            stderr=tb,
            failed_at=datetime.now(UTC).isoformat(),
        )
        meta["processing"] = proc_block
        atomic_write_json(sdir / "session.json", meta)

        _put_message(
            comm_queue,
            {
                "type": "failed",
                "session_id": session_id,
                "stage": current_stage,
                "error": str(err),
                "stderr": tb,
            },
        )
