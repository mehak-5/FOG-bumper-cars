"""Pipeline stage registry and execution hooks.

Stages flow:
extracting -> segmenting -> ranking -> building -> done
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("processing.pipeline")


STAGES: tuple[str, ...] = ("extracting", "segmenting", "ranking", "building")

STAGE_PERCENT: dict[str, float] = {
    "extracting": 25.0,
    "segmenting": 50.0,
    "ranking": 75.0,
    "building": 90.0,
    "done": 100.0,
}

# Optional custom stage handlers (for tests and step-by-step feature plug-in)
_STAGE_HANDLERS: dict[str, Callable[[Path, dict[str, Any]], None]] = {}
_DETECTOR_FACTORY: Callable[[], Any] | None = None


def set_detector_factory(factory: Callable[[], Any] | None) -> None:
    global _DETECTOR_FACTORY
    _DETECTOR_FACTORY = factory


def register_stage_handler(stage: str, handler: Callable[[Path, dict[str, Any]], None]) -> None:
    _STAGE_HANDLERS[stage] = handler


def unregister_stage_handler(stage: str) -> None:
    _STAGE_HANDLERS.pop(stage, None)


def execute_stage(stage: str, session_dir: Path, meta: dict[str, Any]) -> None:
    """Execute a single pipeline stage.

    In Step 14, extraction is wired in.
    In Steps 15-16, real stage logic will be plugged in.
    """
    fail_file = session_dir / "fail_stage.txt"
    if fail_file.is_file():
        target = fail_file.read_text(encoding="utf-8").strip()
        if target == stage:
            raise RuntimeError(f"Simulated pipeline failure at stage {stage}")

    if stage in _STAGE_HANDLERS:
        _STAGE_HANDLERS[stage](session_dir, meta)
        return

    if stage == "extracting":
        from backend.app.processing.extraction import extract_session_detections

        detector = _DETECTOR_FACTORY() if _DETECTOR_FACTORY else None
        extract_session_detections(session_dir, meta, detector=detector)
        return

    if stage == "segmenting":
        from backend.app.processing.segmentation import segment_session

        segment_session(session_dir, meta)
        return

    if stage == "ranking":
        from backend.app.processing.ranking import rank_and_select_moments

        rank_and_select_moments(session_dir, meta)
        return

    if stage == "building":
        from backend.app.reel.builder import build_reel

        build_reel(session_dir, meta)
        return

    log.debug("Unknown stage %s in %s", stage, session_dir.name)
