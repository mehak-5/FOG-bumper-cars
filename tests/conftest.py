"""Shared fixtures: a fully wired operator app with fake ffmpeg processes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from tests.fakes import ReadyPreview, Spawner

VENUE_YAML = """\
venue:
  name: Test Venue
recording:
  open_timeout_seconds: 0.5
  disconnect_timeout_seconds: 0.5
  max_duration_seconds: 30
cameras:
  - id: camera_1
    name: Front
    source: a.mp4
  - id: camera_2
    name: Rear
    source: b.mp4
"""

VALID_ID = "20260101_120000_abcdef"
WS = "ws://127.0.0.1:8000/ws"  # TestClient ignores base_url for websockets


@pytest.fixture
def venue_path(tmp_path):
    p = tmp_path / "venue.yaml"
    p.write_text(VENUE_YAML, encoding="utf-8")
    return p


@pytest.fixture
def quits():
    return []


@pytest.fixture
def operator_app(tmp_path, venue_path, quits):
    app = create_app(
        config_path=venue_path,
        app_root=tmp_path,
        data_root=tmp_path,
        spawn_fn=Spawner(),
        check_ffmpeg=False,
        start_preview=False,
        start_processing=False,
        on_quit=lambda: quits.append(True),
        camera_opts={"stop_timeout": 0.3, "poll_interval": 0.05},
    )
    # Real previews need ffmpeg; the readiness guard is covered in test_recorder_manager.
    app.state.ctx.recorder._preview = ReadyPreview(["camera_1", "camera_2"])
    return app


@pytest.fixture
def client(operator_app):
    # Host must be a loopback name: the app rejects anything else (DNS-rebinding defence).
    with TestClient(operator_app, base_url="http://127.0.0.1:8000") as c:
        yield c


@pytest.fixture
def ctx(operator_app):
    return operator_app.state.ctx
