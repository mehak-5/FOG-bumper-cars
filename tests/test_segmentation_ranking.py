"""Tests for Step 15: segmentation, feature extraction, scoring, diversity and moment ranking."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.app.processing.ranking import (
    calculate_score,
    effective_duration,
    generate_evenly_spaced_clips,
    has_gap_conflict,
    rank_and_select_moments,
    select_moments,
)
from backend.app.processing.segmentation import (
    classify_event,
    compute_iou,
    detect_peaks,
    extract_bin_features,
    merge_and_clamp_intervals,
    segment_session,
)


def test_compute_iou() -> None:
    # Identical
    assert compute_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    # No overlap
    assert compute_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    # Partial overlap: box1 0..10x0..10 (area 100), box2 5..15x0..10 (area 100). Inter: 5x10=50. Union: 150. IOU: 50/150 = 1/3
    iou = compute_iou((0, 0, 10, 10), (5, 0, 15, 10))
    assert pytest.approx(iou, rel=1e-3) == 1.0 / 3.0


def test_extract_bin_features_and_activity() -> None:
    # Synthetic parquet detections
    rows = [
        # Bin 0 (0-1s)
        {
            "ride_time_sec": 0.2,
            "camera_id": "cam_a",
            "track_id": 1,
            "center_x": 100.0,
            "center_y": 100.0,
            "x1": 80.0,
            "y1": 80.0,
            "x2": 120.0,
            "y2": 120.0,
            "confidence": 0.9,
        },
        {
            "ride_time_sec": 0.5,
            "camera_id": "cam_a",
            "track_id": 1,
            "center_x": 150.0,
            "center_y": 100.0,
            "x1": 130.0,
            "y1": 80.0,
            "x2": 170.0,
            "y2": 120.0,
            "confidence": 0.85,
        },
        # Bin 1 (1-2s) with collision (high iou, close centers)
        {
            "ride_time_sec": 1.2,
            "camera_id": "cam_a",
            "track_id": 1,
            "center_x": 200.0,
            "center_y": 100.0,
            "x1": 180.0,
            "y1": 80.0,
            "x2": 220.0,
            "y2": 120.0,
            "confidence": 0.95,
        },
        {
            "ride_time_sec": 1.2,
            "camera_id": "cam_b",
            "track_id": 2,
            "center_x": 205.0,
            "center_y": 102.0,
            "x1": 185.0,
            "y1": 82.0,
            "x2": 225.0,
            "y2": 122.0,
            "confidence": 0.92,
        },
    ]
    df = pd.DataFrame(rows)
    bins = extract_bin_features(df, max_time=3.0, bin_size=1.0)
    assert len(bins) == 3
    assert bins[0]["object_count"] == 1
    assert bins[1]["object_count"] == 2
    assert bins[1]["iou_max"] > 0.5
    assert bins[1]["camera_agreement"] == 1.0  # both cameras present


def test_classify_event() -> None:
    ev_type, score = classify_event({"iou_max": 0.25, "accel": 10.0})
    assert ev_type == "collision_candidate"
    assert score >= 0.6

    ev_type, score = classify_event({"iou_max": 0.05, "proximity": 0.5})
    assert ev_type == "near_miss"

    ev_type, score = classify_event({"iou_max": 0.0, "proximity": 0.0, "speed": 30.0})
    assert ev_type == "high_speed"

    ev_type, score = classify_event(
        {"iou_max": 0.0, "proximity": 0.0, "speed": 5.0, "object_count": 3}
    )
    assert ev_type == "crowd"


def test_merge_and_clamp_intervals() -> None:
    # Interval shorter than MIN_MOMENT_SECONDS (2.0s) gets expanded
    raw = [(1.0, 1.8, 1.4, 1)]  # dur 0.8s
    merged = merge_and_clamp_intervals(raw, total_duration=20.0)
    assert len(merged) == 1
    start, end, peak, _ = merged[0]
    assert round(end - start, 1) >= 2.0

    # Overlapping intervals get merged
    raw = [(1.0, 4.0, 2.5, 2), (3.0, 6.0, 4.5, 4)]
    merged = merge_and_clamp_intervals(raw, total_duration=20.0)
    assert len(merged) == 1
    assert merged[0][0] == 1.0
    assert merged[0][1] == 6.0

    # Interval longer than MAX_MOMENT_SECONDS (8.0s) gets clamped
    raw = [(1.0, 12.0, 6.0, 6)]
    merged = merge_and_clamp_intervals(raw, total_duration=20.0)
    assert len(merged) == 1
    assert merged[0][1] - merged[0][0] <= 8.0


def test_detect_peaks() -> None:
    signal = [0.1, 0.2, 0.9, 0.3, 0.1, 0.8, 0.2]
    peaks = detect_peaks(signal, threshold_factor=0.0)
    assert 2 in peaks
    assert 5 in peaks


def test_calculate_score() -> None:
    # S = 0.35E + 0.15D + 0.25M + 0.15A + 0.10C
    cand = {
        "event_score": 1.0,
        "density_score": 1.0,
        "motion_score": 1.0,
        "camera_agreement": 1.0,
        "confidence_score": 1.0,
    }
    assert calculate_score(cand) == 1.0

    cand_zero = {
        "event_score": 0.0,
        "density_score": 0.0,
        "motion_score": 0.0,
        "camera_agreement": 0.0,
        "confidence_score": 0.0,
    }
    assert calculate_score(cand_zero) == 0.0


def test_effective_duration_and_gap_conflict() -> None:
    moments = [
        {"duration_sec": 5.0, "start_sec": 0.0, "end_sec": 5.0},
        {"duration_sec": 5.0, "start_sec": 10.0, "end_sec": 15.0},
    ]
    # 2 clips of 5.0s joined with 0.3s crossfade: 10.0 - 0.3 = 9.7s
    assert effective_duration(moments, 0.3) == 9.7

    # Check gap conflict
    selected = [{"start_sec": 10.0, "end_sec": 15.0}]
    assert has_gap_conflict({"start_sec": 16.0, "end_sec": 20.0}, selected, min_gap=3.0) is True
    assert has_gap_conflict({"start_sec": 18.0, "end_sec": 22.0}, selected, min_gap=3.0) is False


def test_select_moments_diversity_and_fallback() -> None:
    # Test fallback generator directly
    clips = generate_evenly_spaced_clips(total_duration=30.0, target_duration=12.0, clip_len=4.0)
    assert len(clips) >= 2

    # Fallback to evenly spaced clips when below 15s floor
    few_candidates = [
        {
            "id": "m1",
            "start_sec": 1.0,
            "end_sec": 4.0,
            "duration_sec": 3.0,
            "peak_sec": 2.5,
            "event_score": 0.8,
            "density_score": 0.5,
            "motion_score": 0.5,
            "camera_agreement": 0.5,
            "confidence_score": 0.8,
            "event_type": "collision",
        }
    ]
    selected = select_moments(few_candidates, total_duration=60.0, min_floor_seconds=15.0)
    # Should trigger fallback
    assert len(selected) > 1
    assert all("fallback" in s.get("event_type", "") for s in selected)

    # Diversity constraint: max 3 per event_type
    crowd_candidates = [
        {
            "id": f"m_{i}",
            "start_sec": float(i * 10),
            "end_sec": float(i * 10 + 4),
            "duration_sec": 4.0,
            "peak_sec": float(i * 10 + 2),
            "event_score": 0.9 - i * 0.01,
            "density_score": 0.9,
            "motion_score": 0.9,
            "camera_agreement": 0.9,
            "confidence_score": 0.9,
            "event_type": "crowd",
        }
        for i in range(6)
    ]
    selected_diversity = select_moments(
        crowd_candidates, total_duration=100.0, max_per_event_type=3
    )
    crowd_selected = [s for s in selected_diversity if s.get("event_type") == "crowd"]
    assert len(crowd_selected) <= 3


def test_full_segmentation_and_ranking_flow(tmp_path: Path) -> None:
    session_dir = tmp_path / "test_session"
    session_dir.mkdir(parents=True)

    # Create synthetic detections.parquet
    data = []
    for t in [2.0, 2.5, 3.0, 15.0, 15.5, 30.0, 30.5, 45.0, 45.5]:
        data.append(
            {
                "ride_time_sec": t,
                "camera_id": "cam_1",
                "track_id": 1,
                "center_x": 100.0,
                "center_y": 100.0,
                "x1": 80.0,
                "y1": 80.0,
                "x2": 120.0,
                "y2": 120.0,
                "confidence": 0.9,
            }
        )
        data.append(
            {
                "ride_time_sec": t,
                "camera_id": "cam_2",
                "track_id": 2,
                "center_x": 105.0,
                "center_y": 105.0,
                "x1": 85.0,
                "y1": 85.0,
                "x2": 125.0,
                "y2": 125.0,
                "confidence": 0.88,
            }
        )
    df = pd.DataFrame(data)
    df.to_parquet(session_dir / "detections.parquet")

    meta = {
        "session_id": "test_session",
        "duration_seconds": 60.0,
        "cameras": [{"id": "cam_1"}, {"id": "cam_2"}],
    }

    # Step 1: Segment
    moments_path = segment_session(session_dir, meta)
    assert moments_path.is_file()
    doc = json.loads(moments_path.read_text(encoding="utf-8"))
    assert doc["candidate_count"] > 0
    assert len(doc["candidates"]) > 0

    # Step 2: Rank & Select
    out_path = rank_and_select_moments(session_dir, meta)
    assert out_path == moments_path
    updated = json.loads(out_path.read_text(encoding="utf-8"))
    assert updated["selected_count"] > 0
    assert len(updated["selected_moments"]) > 0
    # Chronological sort
    starts = [m["start_sec"] for m in updated["selected_moments"]]
    assert starts == sorted(starts)
