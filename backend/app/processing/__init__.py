"""Background AI processing and job queue package."""

from backend.app.processing.extraction import (
    DETECTIONS_COLUMNS,
    DetectionBox,
    Detector,
    DummyDetector,
    YOLODetector,
    extract_camera_video,
    extract_session_detections,
)
from backend.app.processing.pipeline import (
    STAGE_PERCENT,
    STAGES,
    execute_stage,
    register_stage_handler,
    set_detector_factory,
    unregister_stage_handler,
)
from backend.app.processing.ranking import (
    calculate_score,
    effective_duration,
    rank_and_select_moments,
    select_moments,
)
from backend.app.processing.segmentation import (
    MomentCandidate,
    classify_event,
    detect_peaks,
    segment_session,
)
from backend.app.processing.service import ProcessingService, SpawnFn
from backend.app.processing.worker import run_job

__all__ = [
    "DETECTIONS_COLUMNS",
    "DetectionBox",
    "Detector",
    "DummyDetector",
    "MomentCandidate",
    "ProcessingService",
    "STAGE_PERCENT",
    "STAGES",
    "SpawnFn",
    "YOLODetector",
    "calculate_score",
    "classify_event",
    "detect_peaks",
    "effective_duration",
    "execute_stage",
    "extract_camera_video",
    "extract_session_detections",
    "rank_and_select_moments",
    "register_stage_handler",
    "run_job",
    "select_moments",
    "segment_session",
    "set_detector_factory",
    "unregister_stage_handler",
]
