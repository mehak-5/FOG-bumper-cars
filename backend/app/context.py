"""Everything the routes and WebSocket handlers share, built once by the app factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.config import AppConfig
from backend.app.core.events import EventBus
from backend.app.core.logger import BusLogHandler
from backend.app.recorder.preview import PreviewManager
from backend.app.recorder.recorder_manager import RecorderManager
from backend.app.triggers.keyboard_trigger import KeyboardTrigger
from backend.app.ws.connection_manager import ConnectionManager

if TYPE_CHECKING:
    from backend.app.processing.service import ProcessingService
    from backend.app.services.publisher import ReelPublisher
    from backend.app.services.share import ShareService
    from backend.app.services.tunnel import TunnelManager


@dataclass
class AppContext:
    cfg: AppConfig
    config_path: Path
    app_root: Path
    data_root: Path
    bus: EventBus
    recorder: RecorderManager
    preview: PreviewManager
    trigger: KeyboardTrigger
    ws: ConnectionManager
    fatal: str | None = None
    log_handler: BusLogHandler | None = None
    processing: ProcessingService | None = None
    share: ShareService | None = None
    tunnel: TunnelManager | None = None
    publisher: ReelPublisher | None = None
    extra: dict[str, object] = field(default_factory=dict)
