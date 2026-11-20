"""Tests for Step 14: Object detection and tracking extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import pytest

from backend.app.core.atomic import atomic_write_json
from backend.app.processing.extraction import (
    DETECTIONS_COLUMNS,
    DetectionBox,
    extract_camera_video,
    extract_session_detections,
    make_empty_detections_df,
)
from backend.app.processing.pipeline import (
    execute_stage,
    set_detector_factory,
)


class FakeDetector:
    def __init__(self, boxes: list[DetectionBox] | None = None) -> None:
        self.boxes = (
            boxes
            if boxes is not None
            else [
                DetectionBox(
                    track_id=1,
                    class_id=0,
                    class_name="bumper_car",
                    confidence=0.88,
                    x1=100.0,
                    y1=150.0,
                    x2=200.0,
                    y2=250.0,
                ),
                DetectionBox(
                    track_id=2,
                    class_id=1,
                    class_name="person",
                    confidence=0.75,
                    x1=300.0,
                    y1=200.0,
                    x2=350.0,
                    y2=320.0,
                ),
            ]
        )
        self.reset_called = False

    def track_frame(self, _frame: Any) -> list[DetectionBox]:
        return list(self.boxes)

    def reset(self) -> None:
        self.reset_called = True


def make_test_video(path: Path, num_frames: int = 15, fps: float = 25.0) -> Path:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(path), fourcc, fps, (320, 240))
    for i in range(num_frames):
        frame = np.full((240, 320, 3), (i * 15) % 256, dtype=np.uint8)
        out.write(frame)
    out.release()
    return path


def test_empty_dataframe_has_canonical_schema():
    df = make_empty_detections_df()
    assert list(df.columns) == DETECTIONS_COLUMNS
    assert len(df) == 0


def test_extract_camera_video_frame_stride_and_geometry(tmp_path):
    vid = make_test_video(tmp_path / "cam1.mp4", num_frames=15, fps=25.0)
    detector = FakeDetector()

    records = extract_camera_video(
        video_path=vid,
        camera_id="camera_1",
        offset_sec=0.5,
        detector=detector,
        frame_stride=3,
    )

    # 15 frames with stride 3 -> sampled frames: 0, 3, 6, 9, 12 (5 sampled frames)
    # 2 boxes per sampled frame -> 10 records
    assert len(records) == 10
    assert detector.reset_called is True

    frame_indices = sorted({r["frame_idx"] for r in records})
    assert frame_indices == [0, 3, 6, 9, 12]

    # Verify first detection geometry and center coordinates
    r0 = records[0]
    assert r0["camera_id"] == "camera_1"
    assert r0["video_time_sec"] == 0.0
    assert r0["ride_time_sec"] == 0.5  # 0.0 + offset (0.5)
    assert r0["center_x"] == 150.0  # (100 + 200) / 2
    assert r0["center_y"] == 200.0  # (150 + 250) / 2
    assert r0["width"] == 100.0
    assert r0["height"] == 100.0


def test_extract_session_detections_multi_camera_alignment(tmp_path):
    sdir = tmp_path / "ride_20260101_120000_123456"
    sdir.mkdir(parents=True, exist_ok=True)

    make_test_video(sdir / "cam1.mp4", num_frames=10, fps=25.0)
    make_test_video(sdir / "cam2.mp4", num_frames=10, fps=25.0)

    meta = {
        "session_id": "20260101_120000_123456",
        "cameras": [
            {"id": "camera_1", "file": "cam1.mp4", "offset_sec": 0.0},
            {"id": "camera_2", "file": "cam2.mp4", "offset_sec": 1.25},
        ],
    }
    atomic_write_json(sdir / "session.json", meta)

    detector = FakeDetector()
    parquet_path = extract_session_detections(sdir, meta, detector=detector, frame_stride=3)
    assert parquet_path.is_file()

    df = pd.read_parquet(parquet_path)
    assert list(df.columns) == DETECTIONS_COLUMNS
    assert set(df["camera_id"].unique()) == {"camera_1", "camera_2"}

    # Camera 2 ride times should be shifted by offset_sec 1.25
    cam2_rows = df[df["camera_id"] == "camera_2"]
    assert pytest.approx(cam2_rows.iloc[0]["ride_time_sec"], rel=1e-3) == (
        cam2_rows.iloc[0]["video_time_sec"] + 1.25
    )


def test_extract_session_with_missing_files_writes_empty_parquet(tmp_path):
    sdir = tmp_path / "ride_20260101_120000_empty"
    sdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": "20260101_120000_empty",
        "cameras": [
            {"id": "cam_a", "file": "missing.mp4", "offset_sec": 0.0},
        ],
    }
    detector = FakeDetector()
    parquet_path = extract_session_detections(sdir, meta, detector=detector)
    assert parquet_path.is_file()

    df = pd.read_parquet(parquet_path)
    assert len(df) == 0
    assert list(df.columns) == DETECTIONS_COLUMNS


def test_pipeline_stage_extracting_integration(tmp_path):
    sdir = tmp_path / "ride_20260101_120000_pipe"
    sdir.mkdir(parents=True, exist_ok=True)
    make_test_video(sdir / "cam.mp4", num_frames=6, fps=25.0)
    meta = {
        "session_id": "20260101_120000_pipe",
        "cameras": [{"id": "c1", "file": "cam.mp4", "offset_sec": 0.1}],
    }

    try:
        set_detector_factory(FakeDetector)
        execute_stage("extracting", sdir, meta)
        parquet_file = sdir / "detections.parquet"
        assert parquet_file.is_file()
        df = pd.read_parquet(parquet_file)
        assert len(df) > 0
    finally:
        set_detector_factory(None)
