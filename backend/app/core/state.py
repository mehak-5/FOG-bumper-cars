from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class RideStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"


class CameraStatus(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class CameraState(BaseModel):
    id: str
    name: str
    status: CameraStatus = CameraStatus.IDLE
    frames: int = 0
    file: Optional[str] = None
    error: Optional[str] = None


class CameraReadiness(BaseModel):
    ready: bool = False
    reason: str = "no frame yet"
    last_frame_at: Optional[float] = None


class TriggerState(BaseModel):
    source: str = "none"
    last_key: Optional[str] = None
    last_accepted: Optional[bool] = None
    last_reason: Optional[str] = None


class AppState(BaseModel):
    venue: str = "Venue"
    ride: RideStatus = RideStatus.IDLE
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    cameras: list[CameraState] = Field(default_factory=list)
    last_session: Optional[dict[str, Any]] = None
    readiness: dict[str, CameraReadiness] = Field(default_factory=dict)
    trigger: Optional[TriggerState] = None
    ride_ends_at: Optional[float] = None
    disk_free_gb: Optional[float] = None
    notes: str = ""
