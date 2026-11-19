"""Loads and validates config/venue.yaml (Pydantic). Port of src/backend/services/config.ts."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator

from backend.app.core.atomic import atomic_write_text


class WebcamSource(BaseModel):
    type: Literal["webcam"] = "webcam"
    index: int | None = None
    name: str | None = None


class StreamSource(BaseModel):
    type: Literal["stream"] = "stream"
    url: str


class FileSource(BaseModel):
    type: Literal["file"] = "file"
    path: str


Source = WebcamSource | StreamSource | FileSource

_FILE_EXT = re.compile(r"\.(mp4|mkv|avi|mov|ts)$", re.I)
_URL = re.compile(r"^[a-z]+://", re.I)
_CAMERA_ID = re.compile(r"^[\w-]+$")


def normalize_source(raw: object, app_root: Path) -> Source:
    """Classify a venue.yaml `source` value: webcam index/name, stream URL, or dev video file."""
    text = str(raw).strip()
    if text.isdigit():
        return WebcamSource(index=int(text))
    if _URL.match(text):
        return StreamSource(url=text)
    if _FILE_EXT.search(text):
        p = Path(text)
        return FileSource(path=str(p if p.is_absolute() else app_root / p))
    return WebcamSource(name=text)


class RecordingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ffmpeg_path: str = "ffmpeg"
    width: PositiveInt = 1280
    height: PositiveInt = 720
    fps: PositiveInt = 25
    format: str = "mp4"
    video_codec: str = "libx264"
    encoder_args: list[str] = Field(default_factory=lambda: ["-preset", "veryfast", "-crf", "23"])
    ride_duration_seconds: PositiveFloat | None = None
    max_duration_seconds: PositiveFloat | None = 600
    open_timeout_seconds: PositiveFloat = 10
    disconnect_timeout_seconds: PositiveFloat = 5


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debounce_seconds: Annotated[float, Field(ge=0)] = 0.5


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions_dir: str = "storage/sessions"
    disk_warn_gb: Annotated[float, Field(ge=0)] = 5


class VenueInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None


class RawCamera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    source: int | str
    enabled: bool = True
    input_args: list[str] | None = None

    @field_validator("id")
    @classmethod
    def _id_chars(cls, v: str) -> str:
        if not _CAMERA_ID.match(v):
            raise ValueError("camera id may only contain letters, digits, _ and -")
        return v

    @field_validator("source")
    @classmethod
    def _source_not_empty(cls, v: int | str) -> int | str:
        if isinstance(v, str) and not v.strip():
            raise ValueError("camera source must not be empty")
        return v


class VenueFile(BaseModel):
    """Schema of venue.yaml itself (what is validated and what the UI writes back)."""

    model_config = ConfigDict(extra="forbid")

    venue: VenueInfo = Field(default_factory=VenueInfo)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    cameras: list[RawCamera] = Field(default_factory=list)


class CameraConfig(BaseModel):
    id: str
    name: str
    source: Source
    input_args: list[str] | None = None


class AppConfig(BaseModel):
    venue: VenueInfo
    recording: RecordingConfig
    trigger: TriggerConfig
    sessions_dir: Path
    cameras: list[CameraConfig]
    disk_warn_gb: float


class ConfigError(ValueError):
    """Raised for any unreadable or invalid venue config."""


def _source_key(src: Source) -> str | None:
    if isinstance(src, StreamSource):
        return src.url
    if isinstance(src, WebcamSource):
        return f"webcam:{src.index if src.index is not None else src.name}"
    return None  # file sources may legitimately repeat (dev loops)


def load_config(path: str | Path, app_root: str | Path, data_root: str | Path) -> AppConfig:
    path, app_root, data_root = Path(path), Path(app_root), Path(data_root)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as err:
        raise ConfigError(f"Cannot read config {path}: {err}") from err
    try:
        doc = VenueFile.model_validate(raw)
    except ValueError as err:
        raise ConfigError(f"Invalid config {path}: {err}") from err

    ids: set[str] = set()
    for cam in doc.cameras:
        if cam.id in ids:
            raise ConfigError(f"Camera id '{cam.id}' is not unique")
        ids.add(cam.id)

    recording = doc.recording
    if env_ffmpeg := os.environ.get("FFMPEG_PATH"):
        recording = recording.model_copy(update={"ffmpeg_path": env_ffmpeg})

    cameras = [
        CameraConfig(
            id=c.id,
            name=c.name,
            source=normalize_source(c.source, app_root),
            input_args=c.input_args,
        )
        for c in doc.cameras
        if c.enabled
    ]
    if not cameras:
        raise ConfigError("No enabled cameras in configuration")

    # Two recorders on one device/stream would compete for the connection.
    seen: set[str] = set()
    for cam in cameras:
        key = _source_key(cam.source)
        if key is None:
            continue
        if key in seen:
            raise ConfigError(f"Camera '{cam.id}' uses the same source as another enabled camera")
        seen.add(key)

    sessions = Path(doc.storage.sessions_dir)
    return AppConfig(
        venue=doc.venue,
        recording=recording,
        trigger=doc.trigger,
        sessions_dir=sessions if sessions.is_absolute() else data_root / sessions,
        cameras=cameras,
        disk_warn_gb=doc.storage.disk_warn_gb,
    )


def save_config_text(path: str | Path, text: str) -> None:
    """Validate then atomically replace venue.yaml (tmp -> fsync -> os.replace).

    Nothing is written if the new text fails schema validation.
    """
    path = Path(path)
    try:
        VenueFile.model_validate(yaml.safe_load(text) or {})
    except (ValueError, yaml.YAMLError) as err:
        raise ConfigError(f"Refusing to save invalid config: {err}") from err
    atomic_write_text(path, text)
