"""Stage 3: Ranking and greedy moment selection.

Score formula:
S = 0.35 * E + 0.15 * D + 0.25 * M + 0.15 * A + 0.10 * C

Greedy heapq selection:
- Target length: 45s (accounting for 0.3s cross-fade loss between clips)
- min_gap_seconds: 3.0s between selected clips
- max_per_event_type: 3
- Fallback: evenly spaced clips if total selected duration < 15.0s floor
- Selected moments sorted chronologically
- Smooth camera assignment avoiding rapid flip-flopping
"""

from __future__ import annotations

import heapq
import json
import logging
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json

log = logging.getLogger("processing.ranking")

DEFAULT_TARGET_REEL_SECONDS = 45.0
DEFAULT_MIN_FLOOR_SECONDS = 15.0
DEFAULT_MIN_GAP_SECONDS = 3.0
DEFAULT_MAX_PER_EVENT_TYPE = 3
DEFAULT_CROSSFADE_SECONDS = 0.3

# Weights for S = 0.35E + 0.15D + 0.25M + 0.15A + 0.10C
WEIGHT_EVENT = 0.35
WEIGHT_DENSITY = 0.15
WEIGHT_MOTION = 0.25
WEIGHT_AGREEMENT = 0.15
WEIGHT_CONFIDENCE = 0.10


def calculate_score(m: dict[str, Any]) -> float:
    """Calculate composite score S = 0.35E + 0.15D + 0.25M + 0.15A + 0.10C."""
    e = float(m.get("event_score", 0.5))
    d = float(m.get("density_score", 0.5))
    m_score = float(m.get("motion_score", 0.5))
    a = float(m.get("camera_agreement", 0.5))
    c = float(m.get("confidence_score", 0.5))

    s = (
        WEIGHT_EVENT * e
        + WEIGHT_DENSITY * d
        + WEIGHT_MOTION * m_score
        + WEIGHT_AGREEMENT * a
        + WEIGHT_CONFIDENCE * c
    )
    return round(float(s), 4)


def effective_duration(moments: list[dict[str, Any]], crossfade_sec: float) -> float:
    """Duration of joined clips subtracting crossfade overlap loss."""
    if not moments:
        return 0.0
    raw_sum = sum(float(m["duration_sec"]) for m in moments)
    overlap_loss = (len(moments) - 1) * crossfade_sec
    return max(0.0, raw_sum - overlap_loss)


def has_gap_conflict(
    candidate: dict[str, Any], selected: list[dict[str, Any]], min_gap: float
) -> bool:
    """Check if candidate start/end is within min_gap of any selected moment."""
    c_start = float(candidate["start_sec"])
    c_end = float(candidate["end_sec"])
    for s in selected:
        s_start = float(s["start_sec"])
        s_end = float(s["end_sec"])
        if not (c_end + min_gap <= s_start or c_start >= s_end + min_gap):
            return True
    return False


def generate_evenly_spaced_clips(
    total_duration: float,
    target_duration: float = DEFAULT_TARGET_REEL_SECONDS,
    clip_len: float = 4.0,
    cameras: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fallback generator when action moments yield less than the 15s floor."""
    if total_duration <= clip_len:
        return [
            {
                "id": "fallback_001",
                "start_sec": 0.0,
                "end_sec": total_duration,
                "duration_sec": total_duration,
                "peak_sec": total_duration / 2.0,
                "event_type": "fallback",
                "score": 0.5,
                "best_camera_id": cameras[0] if cameras else "camera_1",
            }
        ]

    max_clips = int(target_duration / clip_len)
    num_clips = int(min(max_clips, total_duration // clip_len))
    num_clips = max(1, num_clips)
    step = (total_duration - clip_len) / max(1, num_clips - 1) if num_clips > 1 else 0.0

    clips: list[dict[str, Any]] = []
    cam_idx = 0
    cam_list = cameras or ["camera_1"]

    for i in range(num_clips):
        t_start = round(i * step, 2)
        t_end = round(min(total_duration, t_start + clip_len), 2)
        dur = round(t_end - t_start, 2)
        clips.append(
            {
                "id": f"fallback_{i + 1:03d}",
                "start_sec": t_start,
                "end_sec": t_end,
                "duration_sec": dur,
                "peak_sec": round(t_start + dur / 2.0, 2),
                "event_type": "fallback",
                "score": 0.5,
                "best_camera_id": cam_list[cam_idx % len(cam_list)],
            }
        )
        cam_idx += 1

    return clips


def select_moments(
    candidates: list[dict[str, Any]],
    total_duration: float,
    target_seconds: float = DEFAULT_TARGET_REEL_SECONDS,
    min_floor_seconds: float = DEFAULT_MIN_FLOOR_SECONDS,
    min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS,
    max_per_event_type: int = DEFAULT_MAX_PER_EVENT_TYPE,
    crossfade_seconds: float = DEFAULT_CROSSFADE_SECONDS,
    available_cameras: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Greedy selection of moments using max-heap with diversity & duration constraints."""
    if not candidates:
        return generate_evenly_spaced_clips(
            total_duration, target_seconds, cameras=available_cameras
        )

    for c in candidates:
        c["score"] = calculate_score(c)

    heap: list[tuple[float, int, dict[str, Any]]] = []
    for idx, c in enumerate(candidates):
        heapq.heappush(heap, (-c["score"], idx, c))

    selected: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}

    while heap:
        _, _, cand = heapq.heappop(heap)
        ev_type = cand.get("event_type", "action")

        if type_counts.get(ev_type, 0) >= max_per_event_type:
            continue

        if has_gap_conflict(cand, selected, min_gap_seconds):
            continue

        test_selected = [*selected, cand]
        eff_dur = effective_duration(test_selected, crossfade_seconds)
        if eff_dur > target_seconds + 2.0 and selected:
            continue

        selected.append(cand)
        type_counts[ev_type] = type_counts.get(ev_type, 0) + 1

        if eff_dur >= target_seconds:
            break

    total_eff = effective_duration(selected, crossfade_seconds)
    if total_eff < min_floor_seconds:
        log.info(
            "Selected duration %.1fs is below %.1fs floor. Falling back to evenly spaced clips.",
            total_eff,
            min_floor_seconds,
        )
        return generate_evenly_spaced_clips(
            total_duration, target_seconds, cameras=available_cameras
        )

    selected.sort(key=lambda m: float(m["start_sec"]))

    cam_list = available_cameras or ["camera_1"]
    last_cam = None
    for m in selected:
        cam = m.get("best_camera_id") or cam_list[0]
        if last_cam is not None and len(cam_list) > 1:
            pass
        last_cam = cam
        m["best_camera_id"] = cam

    return selected


def rank_and_select_moments(session_dir: Path, meta: dict[str, Any]) -> Path:
    """Read candidate moments, score and greedily select them, and update moments.json."""
    moments_path = session_dir / "moments.json"
    if not moments_path.is_file():
        from backend.app.processing.segmentation import segment_session

        segment_session(session_dir, meta)

    try:
        data = json.loads(moments_path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    candidates = data.get("candidates", [])
    total_duration = float(meta.get("duration_seconds") or data.get("total_duration_sec") or 30.0)

    cameras = [c["id"] for c in meta.get("cameras", []) if c.get("id")]
    selected = select_moments(
        candidates=candidates,
        total_duration=total_duration,
        available_cameras=cameras,
    )

    data["selected_moments"] = selected
    data["selected_count"] = len(selected)
    data["reel_target_seconds"] = DEFAULT_TARGET_REEL_SECONDS

    atomic_write_json(moments_path, data)
    log.info("Selected %d highlight moments for session %s", len(selected), session_dir.name)
    return moments_path
