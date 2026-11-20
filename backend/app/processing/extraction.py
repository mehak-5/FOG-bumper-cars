"""Stage 1: Extraction - YOLO + ByteTrack object detection and tracking.

Extracts bounding boxes and persistent tracking IDs for bumper cars and persons
across all cameras, aligned to a shared ride timeline using camera offset_sec.
Saves detections to detections.parquet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import pandas as pd

log = logging.getLogger("processing.extraction")

DEFAULT_CONF = 0.35
DEFAULT_FRAME_STRIDE = 3
DEFAULT_TRACKER = "bytetrack.yaml"

DETECTIONS_COLUMNS = [
    "camera_id",
    "frame_idx",
    "video_time_sec",
    "ride_time_sec",
    "track_id",
    "class_id",
    "class_name",
    "confidence",
    "x1",
    "y1",
    "x2",
    "y2",
    "center_x",
    "center_y",
    "width",
    "height",
]


@dataclass
class DetectionBox:
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float


class Detector(Protocol):
    def track_frame(self, frame: Any) -> list[DetectionBox]: ...
    def reset(self) -> None: ...


class DummyDetector:
    """Fallback detector when no weights are on disk (offline resilience, test environments)."""

    def track_frame(self, _frame: Any) -> list[DetectionBox]:
        return []

    def reset(self) -> None:
        pass


class YOLODetector:
    """Ultralytics YOLO wrapper with ByteTrack tracking."""

    def __init__(
        self,
        model_path: str | Path = "backend/models/best.pt",
        conf: float = DEFAULT_CONF,
        tracker: str = DEFAULT_TRACKER,
    ) -> None:
        p = Path(model_path)
        if not p.is_file():
            raise FileNotFoundError(
                f"Model weights not found at {p}. Place weights in backend/models/"
            )
        from ultralytics import YOLO

        self.conf = conf
        self.tracker = tracker
        self.model = YOLO(str(p))

    def track_frame(self, frame: Any) -> list[DetectionBox]:
        results = self.model.track(
            source=frame,
            tracker=self.tracker,
            conf=self.conf,
            persist=True,
            verbose=False,
        )
        boxes_out: list[DetectionBox] = []
        if not results:
            return boxes_out

        res = results[0]
        if res.boxes is None or len(res.boxes) == 0:
            return boxes_out

        names = res.names or {}
        for box in res.boxes:
            conf = float(box.conf[0]) if box.conf is not None else 0.0
            if conf < self.conf:
                continue

            track_id = int(box.id[0]) if box.id is not None else -1
            class_id = int(box.cls[0]) if box.cls is not None else 0
            class_name = str(names.get(class_id, class_id))
            xyxy = [float(v) for v in box.xyxy[0]]

            boxes_out.append(
                DetectionBox(
                    track_id=track_id,
                    class_id=class_id,
                    class_name=class_name,
                    confidence=conf,
                    x1=xyxy[0],
                    y1=xyxy[1],
                    x2=xyxy[2],
                    y2=xyxy[3],
                )
            )
        return boxes_out

    def reset(self) -> None:
        """Reset internal tracker state between videos."""
        if hasattr(self.model, "predictor") and self.model.predictor is not None:
            if hasattr(self.model.predictor, "trackers"):
                self.model.predictor.trackers = []


def make_empty_detections_df() -> pd.DataFrame:
    """Return an empty DataFrame with canonical types."""
    return pd.DataFrame(
        {
            "camera_id": pd.Series(dtype="str"),
            "frame_idx": pd.Series(dtype="int64"),
            "video_time_sec": pd.Series(dtype="float64"),
            "ride_time_sec": pd.Series(dtype="float64"),
            "track_id": pd.Series(dtype="int64"),
            "class_id": pd.Series(dtype="int64"),
            "class_name": pd.Series(dtype="str"),
            "confidence": pd.Series(dtype="float64"),
            "x1": pd.Series(dtype="float64"),
            "y1": pd.Series(dtype="float64"),
            "x2": pd.Series(dtype="float64"),
            "y2": pd.Series(dtype="float64"),
            "center_x": pd.Series(dtype="float64"),
            "center_y": pd.Series(dtype="float64"),
            "width": pd.Series(dtype="float64"),
            "height": pd.Series(dtype="float64"),
        }
    )


def extract_camera_video(
    video_path: Path,
    camera_id: str,
    offset_sec: float,
    detector: Detector,
    frame_stride: int = DEFAULT_FRAME_STRIDE,
) -> list[dict[str, Any]]:
    """Sample frames from a video, run detector, and align to ride timeline."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        log.warning("Could not open video file: %s", video_path)
        return []

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0 or fps > 240:
        fps = 25.0

    records: list[dict[str, Any]] = []
    frame_idx = 0
    detector.reset()

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_stride == 0:
                video_time_sec = round(frame_idx / fps, 3)
                ride_time_sec = round(video_time_sec + offset_sec, 3)
                boxes = detector.track_frame(frame)
                for b in boxes:
                    cx = round((b.x1 + b.x2) / 2.0, 2)
                    cy = round((b.y1 + b.y2) / 2.0, 2)
                    w = round(b.x2 - b.x1, 2)
                    h = round(b.y2 - b.y1, 2)
                    records.append(
                        {
                            "camera_id": camera_id,
                            "frame_idx": frame_idx,
                            "video_time_sec": video_time_sec,
                            "ride_time_sec": ride_time_sec,
                            "track_id": b.track_id,
                            "class_id": b.class_id,
                            "class_name": b.class_name,
                            "confidence": round(b.confidence, 3),
                            "x1": round(b.x1, 2),
                            "y1": round(b.y1, 2),
                            "x2": round(b.x2, 2),
                            "y2": round(b.y2, 2),
                            "center_x": cx,
                            "center_y": cy,
                            "width": w,
                            "height": h,
                        }
                    )
            frame_idx += 1
    finally:
        cap.release()

    return records


def extract_session_detections(
    session_dir: Path,
    meta: dict[str, Any],
    detector: Detector | None = None,
    frame_stride: int = DEFAULT_FRAME_STRIDE,
) -> Path:
    """Extract detections for all cameras in session and save detections.parquet."""
    out_parquet = session_dir / "detections.parquet"
    if detector is None:
        model_path = Path("backend/models/best.pt")
        if model_path.is_file():
            detector = YOLODetector(model_path)
        else:
            log.warning("YOLO weights not found at %s - using fallback detector", model_path)
            detector = DummyDetector()

    cameras = meta.get("cameras", [])
    all_records: list[dict[str, Any]] = []

    for cam in cameras:
        cam_id = cam.get("id")
        file_name = cam.get("file")
        if not cam_id or not file_name:
            continue
        video_path = session_dir / file_name
        if not video_path.is_file():
            log.warning("Video file missing for camera %s: %s", cam_id, video_path)
            continue

        offset = float(cam.get("offset_sec") or 0.0)
        recs = extract_camera_video(
            video_path=video_path,
            camera_id=cam_id,
            offset_sec=offset,
            detector=detector,
            frame_stride=frame_stride,
        )
        all_records.extend(recs)

    if all_records:
        df = pd.DataFrame(all_records)
    else:
        df = make_empty_detections_df()

    # Sort deterministically by ride time, then camera, then track_id
    if not df.empty:
        df.sort_values(by=["ride_time_sec", "camera_id", "track_id"], inplace=True)
        df.reset_index(drop=True, inplace=True)

    df.to_parquet(out_parquet, engine="pyarrow", index=False)
    log.info("Wrote %d detections to %s", len(df), out_parquet.name)
    return out_parquet
