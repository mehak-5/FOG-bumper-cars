"""Stage 2: Segmentation - feature extraction, activity curve, peak detection and moment generation.

Binned into 1-second windows:
- object_count
- speed & accel (sudden speed drop = collision)
- iou_max & proximity
- camera_agreement
- event classification: collision_candidate, near_miss, high_speed, crowd
Pads peaks by 1.5s, merges overlaps, clamps durations to [2.0, 8.0]s.
Saves moments to moments.json.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.app.core.atomic import atomic_write_json

log = logging.getLogger("processing.segmentation")

PRE_PAD_SECONDS = 1.5
POST_PAD_SECONDS = 1.5
MIN_MOMENT_SECONDS = 2.0
MAX_MOMENT_SECONDS = 8.0


@dataclass
class MomentCandidate:
    id: str
    start_sec: float
    end_sec: float
    duration_sec: float
    peak_sec: float
    event_type: str
    event_score: float
    motion_score: float
    density_score: float
    camera_agreement: float
    confidence_score: float
    best_camera_id: str | None = None


def compute_iou(
    box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]
) -> float:
    """Compute Intersection over Union between two (x1, y1, x2, y2) bounding boxes."""
    xa = max(box_a[0], box_b[0])
    ya = max(box_a[1], box_b[1])
    xb = min(box_a[2], box_b[2])
    yb = min(box_a[3], box_b[3])

    inter = max(0.0, xb - xa) * max(0.0, yb - ya)
    if inter <= 0.0:
        return 0.0

    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def extract_bin_features(
    df: pd.DataFrame, max_time: float, bin_size: float = 1.0
) -> list[dict[str, Any]]:
    """Slice detections into 1-second bins and compute activity features."""
    num_bins = int(np.ceil(max_time / bin_size)) if max_time > 0 else 1
    bins: list[dict[str, Any]] = []
    prev_speed: float = 0.0
    has_data = not df.empty and "ride_time_sec" in df.columns

    for b in range(num_bins):
        t_start = b * bin_size
        t_end = t_start + bin_size
        slice_df = (
            df[(df["ride_time_sec"] >= t_start) & (df["ride_time_sec"] < t_end)]
            if has_data
            else pd.DataFrame()
        )

        if slice_df.empty:
            bins.append(
                {
                    "bin_idx": b,
                    "t_center": t_start + bin_size / 2.0,
                    "object_count": 0,
                    "speed": 0.0,
                    "accel": 0.0,
                    "iou_max": 0.0,
                    "proximity": 0.0,
                    "camera_agreement": 0.0,
                    "avg_confidence": 0.0,
                }
            )
            prev_speed = 0.0
            continue

        obj_count = slice_df["track_id"].nunique()

        speeds: list[float] = []
        for _, track_group in slice_df.groupby("track_id"):
            if len(track_group) >= 2:
                sorted_grp = track_group.sort_values("ride_time_sec")
                dx = sorted_grp["center_x"].diff().dropna()
                dy = sorted_grp["center_y"].diff().dropna()
                dt = sorted_grp["ride_time_sec"].diff().dropna().replace(0, 0.04)
                dist = np.sqrt(dx**2 + dy**2)
                sp = (dist / dt).mean()
                speeds.append(float(sp))

        curr_speed = float(np.mean(speeds)) if speeds else 0.0
        accel = max(0.0, prev_speed - curr_speed)
        prev_speed = curr_speed

        boxes = slice_df[["x1", "y1", "x2", "y2"]].to_numpy()
        max_iou = 0.0
        n_boxes = len(boxes)
        if n_boxes >= 2:
            for i in range(min(n_boxes, 15)):
                for j in range(i + 1, min(n_boxes, 15)):
                    iou = compute_iou(tuple(boxes[i]), tuple(boxes[j]))
                    if iou > max_iou:
                        max_iou = iou

        centers = slice_df[["center_x", "center_y"]].to_numpy()
        close_pairs = 0
        if len(centers) >= 2:
            for i in range(min(len(centers), 15)):
                for j in range(i + 1, min(len(centers), 15)):
                    d = np.linalg.norm(centers[i] - centers[j])
                    if d < 120.0:
                        close_pairs += 1
        proximity = min(1.0, close_pairs / 5.0)

        cameras = slice_df["camera_id"].nunique()
        cam_agree = min(1.0, cameras / 2.0)
        avg_conf = float(slice_df["confidence"].mean())

        bins.append(
            {
                "bin_idx": b,
                "t_center": t_start + bin_size / 2.0,
                "object_count": obj_count,
                "speed": curr_speed,
                "accel": accel,
                "iou_max": max_iou,
                "proximity": proximity,
                "camera_agreement": cam_agree,
                "avg_confidence": avg_conf,
            }
        )

    return bins


def normalize_signal(values: list[float]) -> list[float]:
    """Normalize a list of float values to [0, 1]."""
    arr = np.array(values, dtype=float)
    ptp = arr.max() - arr.min()
    if ptp <= 1e-6:
        return [0.0] * len(values) if arr.max() <= 1e-6 else [1.0] * len(values)
    norm = (arr - arr.min()) / ptp
    return [float(x) for x in norm]


def build_activity_signal(bins: list[dict[str, Any]]) -> list[float]:
    """Combine normalized features into a smoothed 1D activity curve."""
    if not bins:
        return []

    norm_counts = normalize_signal([b["object_count"] for b in bins])
    norm_speeds = normalize_signal([b["speed"] for b in bins])
    norm_accels = normalize_signal([b["accel"] for b in bins])
    norm_ious = normalize_signal([b["iou_max"] for b in bins])
    norm_proxs = normalize_signal([b["proximity"] for b in bins])

    raw_signal: list[float] = []
    for i in range(len(bins)):
        val = (
            0.35 * norm_ious[i]
            + 0.25 * norm_accels[i]
            + 0.20 * norm_speeds[i]
            + 0.10 * norm_proxs[i]
            + 0.10 * norm_counts[i]
        )
        raw_signal.append(val)

    smoothed: list[float] = []
    n = len(raw_signal)
    for i in range(n):
        left = raw_signal[max(0, i - 1)]
        mid = raw_signal[i]
        right = raw_signal[min(n - 1, i + 1)]
        smoothed.append(0.25 * left + 0.5 * mid + 0.25 * right)

    return smoothed


def detect_peaks(signal: list[float], threshold_factor: float = 0.3) -> list[int]:
    """Find local maxima in signal above a threshold."""
    if len(signal) < 2:
        return [0] if signal and signal[0] > 0 else []

    mean_val = float(np.mean(signal))
    threshold = max(0.1, mean_val + threshold_factor * float(np.std(signal)))
    peaks: list[int] = []

    for i in range(len(signal)):
        val = signal[i]
        if val < threshold:
            continue
        left = signal[i - 1] if i > 0 else -1.0
        right = signal[i + 1] if i < len(signal) - 1 else -1.0
        if val >= left and val >= right:
            peaks.append(i)

    return peaks


def classify_event(bin_data: dict[str, Any]) -> tuple[str, float]:
    """Classify the primary event type and event score for a peak."""
    iou = bin_data.get("iou_max", 0.0)
    accel = bin_data.get("accel", 0.0)
    speed = bin_data.get("speed", 0.0)
    obj_count = bin_data.get("object_count", 0)

    if iou >= 0.10 or accel > 15.0:
        return "collision_candidate", min(1.0, 0.6 + iou * 2.0)
    if iou > 0.03 or bin_data.get("proximity", 0.0) > 0.4:
        return "near_miss", min(1.0, 0.5 + iou * 3.0)
    if speed > 20.0:
        return "high_speed", min(1.0, 0.4 + speed / 100.0)
    if obj_count >= 2:
        return "crowd", min(1.0, 0.3 + obj_count / 10.0)
    return "high_speed", 0.3


def merge_and_clamp_intervals(
    raw_intervals: list[tuple[float, float, float, int]],
    total_duration: float,
) -> list[tuple[float, float, float, int]]:
    """Merge overlapping intervals and clamp each interval duration to [2.0, 8.0]s."""
    if not raw_intervals:
        return []

    raw_intervals.sort(key=lambda x: x[0])
    merged: list[list[float]] = []

    for start, end, peak, p_idx in raw_intervals:
        if not merged:
            merged.append([start, end, peak, p_idx])
            continue
        prev = merged[-1]
        if start <= prev[1]:
            prev[1] = max(prev[1], end)
        else:
            merged.append([start, end, peak, p_idx])

    results: list[tuple[float, float, float, int]] = []
    for start, end, peak, p_idx in merged:
        dur = end - start
        if dur < MIN_MOMENT_SECONDS:
            diff = MIN_MOMENT_SECONDS - dur
            start = max(0.0, start - diff / 2.0)
            end = min(total_duration, end + diff / 2.0)
            if end - start < MIN_MOMENT_SECONDS and total_duration >= MIN_MOMENT_SECONDS:
                end = min(total_duration, start + MIN_MOMENT_SECONDS)
        elif dur > MAX_MOMENT_SECONDS:
            start = max(0.0, peak - MAX_MOMENT_SECONDS / 2.0)
            end = min(total_duration, start + MAX_MOMENT_SECONDS)
            start = max(0.0, end - MAX_MOMENT_SECONDS)

        results.append((round(start, 2), round(end, 2), round(peak, 2), int(p_idx)))

    return results


def segment_session(session_dir: Path, meta: dict[str, Any]) -> Path:
    """Analyze detections.parquet and write candidate moments to moments.json."""
    parquet_path = session_dir / "detections.parquet"
    out_moments = session_dir / "moments.json"
    duration = float(meta.get("duration_seconds") or 30.0)

    if parquet_path.is_file():
        try:
            df = pd.read_parquet(parquet_path)
        except Exception:
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    if not df.empty and "ride_time_sec" in df.columns:
        max_t = max(duration, float(df["ride_time_sec"].max()))
    else:
        max_t = duration

    bins = extract_bin_features(df, max_time=max_t)
    signal = build_activity_signal(bins)
    peak_indices = detect_peaks(signal)

    raw_intervals: list[tuple[float, float, float, int]] = []
    for p in peak_indices:
        t_peak = bins[p]["t_center"]
        start = max(0.0, t_peak - PRE_PAD_SECONDS)
        end = min(max_t, t_peak + POST_PAD_SECONDS)
        raw_intervals.append((start, end, t_peak, p))

    intervals = merge_and_clamp_intervals(raw_intervals, max_t)

    candidates: list[MomentCandidate] = []
    norm_densities = normalize_signal([b["object_count"] for b in bins])
    norm_motions = normalize_signal([b["speed"] for b in bins])

    for i, (start, end, peak, p_idx) in enumerate(intervals):
        bin_data = bins[p_idx] if p_idx < len(bins) else {}
        event_type, event_score = classify_event(bin_data)
        d_score = norm_densities[p_idx] if p_idx < len(norm_densities) else 0.5
        m_score = norm_motions[p_idx] if p_idx < len(norm_motions) else 0.5
        c_score = float(bin_data.get("avg_confidence", 0.75))
        a_score = float(bin_data.get("camera_agreement", 0.5))

        best_cam = None
        if not df.empty and "camera_id" in df.columns:
            sub = df[(df["ride_time_sec"] >= start) & (df["ride_time_sec"] <= end)]
            if not sub.empty:
                counts = sub["camera_id"].value_counts()
                if not counts.empty:
                    best_cam = str(counts.index[0])

        candidates.append(
            MomentCandidate(
                id=f"moment_{i + 1:03d}",
                start_sec=start,
                end_sec=end,
                duration_sec=round(end - start, 2),
                peak_sec=peak,
                event_type=event_type,
                event_score=round(event_score, 3),
                motion_score=round(m_score, 3),
                density_score=round(d_score, 3),
                camera_agreement=round(a_score, 3),
                confidence_score=round(c_score, 3),
                best_camera_id=best_cam,
            )
        )

    doc = {
        "session_id": meta.get("session_id"),
        "total_duration_sec": max_t,
        "candidate_count": len(candidates),
        "candidates": [asdict(c) for c in candidates],
        "selected_moments": [],
    }

    atomic_write_json(out_moments, doc)
    log.info("Generated %d candidate moments in %s", len(candidates), out_moments.name)
    return out_moments
