import os
from pathlib import Path
from typing import Any, List, Optional, Union
import yaml
from pydantic import BaseModel, Field


class CameraConfig(BaseModel):
    id: str
    name: str
    source: Union[int, str]  # webcam index, name, rtsp url, or test video file path
    enabled: bool = True
    input_args: Optional[List[str]] = None


class RecordingConfig(BaseModel):
    ffmpeg_path: str = "ffmpeg"
    width: int = Field(1280, ge=320)
    height: int = Field(720, ge=240)
    fps: int = Field(25, ge=5, le=60)
    container: str = "mp4"
    video_codec: str = "libx264"
    encoder_args: List[str] = Field(default_factory=lambda: ["-preset", "veryfast", "-crf", "23"])
    ride_duration_seconds: Optional[int] = None
    max_duration_seconds: Optional[int] = 600
    hard_cap_seconds: int = 300
    open_timeout_seconds: int = 5
    disconnect_timeout_seconds: int = 5


class TriggerConfig(BaseModel):
    debounce_seconds: float = 0.5


class StorageConfig(BaseModel):
    root: str = "storage"
    sessions_dir: str = "storage/sessions"
    disk_warn_gb: float = 5.0
    keep_raw_after_reel: bool = True


class VenueConfig(BaseModel):
    name: str = "Demo Venue"
    moments_server_url: str = "http://localhost:8080/health"


class AppConfig(BaseModel):
    venue: VenueConfig = Field(default_factory=VenueConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    cameras: List[CameraConfig] = Field(default_factory=list)


def load_config(config_path: Union[str, Path] = "config/venue.yaml") -> AppConfig:
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found at: {path.resolve()}")
    
    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    # Override ffmpeg_path if environment variable is present
    ffmpeg_env = os.environ.get("FFMPEG_PATH")
    if ffmpeg_env:
        if "recording" not in raw_data:
            raw_data["recording"] = {}
        raw_data["recording"]["ffmpeg_path"] = ffmpeg_env

    return AppConfig.model_validate(raw_data)
