"""Wire contracts shared with the browser: state snapshot and WebSocket messages.

These Pydantic models are the single source of truth. `tools/gen_ts_types.py` turns them into
backend/app/ui/ts/messages.generated.ts so Python and TypeScript types can never drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CameraStatus = Literal["idle", "connecting", "recording", "completed", "failed", "unavailable"]
RideStatus = Literal["idle", "starting", "recording", "stopping"]
NoticeLevel = Literal["info", "warning", "error"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CameraState(Model):
    id: str
    name: str
    status: CameraStatus
    frames: int
    file: str | None  # relative to the session folder
    error: str | None


class CameraReadiness(Model):
    ready: bool
    reason: str
    last_frame_at: float | None = None  # epoch ms


class TriggerState(Model):
    source: Literal["keyboard", "pcb", "button"]
    last_key: str | None
    last_accepted: bool | None
    last_reason: str | None


class LastSession(Model):
    id: str
    status: str


class AppState(Model):
    venue: str
    ride: RideStatus
    session_id: str | None
    started_at: str | None
    cameras: list[CameraState]
    last_session: LastSession | None
    readiness: dict[str, CameraReadiness]
    trigger: TriggerState | None
    ride_ends_at: float | None  # epoch ms, set when a ride has a time limit
    disk_free_gb: float | None
    notes: str


class Notice(Model):
    level: NoticeLevel
    text: str


# ---------------- WebSocket payloads: client -> server ----------------
class Empty(Model):
    pass


class SessionNotes(Model):
    notes: str


class CameraFeedRequest(Model):
    camera_id: str


# ---------------- WebSocket payloads: server -> client ----------------
class PreviewFrame(Model):
    camera_id: str
    data_url: str | None = None


class CameraReadinessMsg(Model):
    readiness: dict[str, CameraReadiness]


class DiskStatus(Model):
    free_gb: float
    warn: bool


class LogEntry(Model):
    level: str
    logger: str
    message: str
    ts: str


class ProcessingProgress(Model):
    session_id: str
    status: str
    percent: float | None = None


class ReelReady(Model):
    session_id: str


class ShareCreated(Model):
    session_id: str
    url: str
    expires_at: str


class TunnelStatus(Model):
    up: bool
    url: str | None = None


class ErrorMsg(Model):
    message: str
    code: str | None = None


CLIENT_MESSAGES: dict[str, type[Model]] = {
    "state.get": Empty,
    "ride.start": Empty,
    "ride.end": Empty,
    "session.notes": SessionNotes,
    "logs.subscribe": Empty,
    "logs.unsubscribe": Empty,
    "ping": Empty,
    "preview.open": CameraFeedRequest,
    "preview.close": CameraFeedRequest,
}

SERVER_MESSAGES: dict[str, type[Model]] = {
    "state": AppState,
    "notice": Notice,
    "preview.frame": PreviewFrame,
    "camera.readiness": CameraReadinessMsg,
    "disk.status": DiskStatus,
    "log": LogEntry,
    "processing.progress": ProcessingProgress,
    "reel.ready": ReelReady,
    "share.created": ShareCreated,
    "tunnel.status": TunnelStatus,
    "app.quit": Empty,
    "error": ErrorMsg,
    "pong": Empty,
}

# Every model that crosses the wire (state + payloads), for the TypeScript generator.
WIRE_MODELS: list[type[Model]] = sorted(
    {
        CameraState,
        CameraReadiness,
        TriggerState,
        LastSession,
        *CLIENT_MESSAGES.values(),
        *SERVER_MESSAGES.values(),
    },
    key=lambda m: m.__name__,
)


class ProtocolError(ValueError):
    """A malformed or unknown WebSocket message."""


def make_envelope(msg_type: str, payload: Model | dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a server -> client envelope, validating the payload against its contract."""
    model = SERVER_MESSAGES.get(msg_type)
    if model is None:
        raise ProtocolError(f"Unknown server message type: {msg_type!r}")
    if isinstance(payload, Model):
        payload = payload.model_dump(mode="json")
    body = model.model_validate(payload or {}).model_dump(mode="json")
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": body,
    }


class Envelope(Model):
    type: str
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


def parse_client_message(raw: object) -> tuple[str, Model]:
    """Validate a client -> server message. Raises ProtocolError on anything unexpected."""
    if not isinstance(raw, dict):
        raise ProtocolError("Message must be a JSON object")
    try:
        env = Envelope.model_validate(raw)
    except ValueError as err:
        raise ProtocolError(f"Bad envelope: {err}") from err
    model = CLIENT_MESSAGES.get(env.type)
    if model is None:
        raise ProtocolError(f"Unknown message type: {env.type!r}")
    try:
        return env.type, model.model_validate(env.payload)
    except ValueError as err:
        raise ProtocolError(f"Bad payload for {env.type!r}: {err}") from err
