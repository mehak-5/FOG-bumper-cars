from pathlib import Path

import pytest

from backend.app.config import (
    ConfigError,
    FileSource,
    StreamSource,
    WebcamSource,
    load_config,
    save_config_text,
)

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "venue.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def load(p: Path):
    return load_config(p, ROOT, ROOT)


def test_live_config_loads():
    cfg = load(ROOT / "config" / "venue.yaml")
    assert [c.id for c in cfg.cameras] == ["camera_1", "camera_2"]
    assert cfg.recording.fps == 25
    assert cfg.trigger.debounce_seconds == 0.5
    assert cfg.disk_warn_gb == 5


def test_test_config_loads_with_defaults_and_file_sources():
    cfg = load(ROOT / "config" / "venue.test.yaml")
    assert cfg.recording.width == 640
    assert cfg.recording.video_codec == "libx264"  # default preserved
    assert all(isinstance(c.source, FileSource) for c in cfg.cameras)


def test_source_normalization(tmp_path):
    p = write(
        tmp_path,
        """
cameras:
  - {id: a, name: A, source: 0}
  - {id: b, name: B, source: "Logitech C920"}
  - {id: c, name: C, source: "rtsp://h/s"}
""",
    )
    a, b, c = load(p).cameras
    assert a.source == WebcamSource(index=0)
    assert b.source == WebcamSource(name="Logitech C920")
    assert c.source == StreamSource(url="rtsp://h/s")


@pytest.mark.parametrize(
    "body",
    [
        "cameras: []",
        "cameras:\n  - {id: a, name: A, source: 0, enabled: false}",
        "cameras:\n  - {id: a, name: A, source: 0}\n  - {id: a, name: B, source: 1}",
        "cameras:\n  - {id: 'bad id', name: A, source: 0}",
        "cameras:\n  - {id: a, name: A, source: 0}\n  - {id: b, name: B, source: 0}",
        "recording: {fps: 0}\ncameras:\n  - {id: a, name: A, source: 0}",
        "server: {port: 8000, public_port: 8000}\ncameras:\n  - {id: a, name: A, source: 0}",
        "server: {port: 80}\ncameras:\n  - {id: a, name: A, source: 0}",
        "recording: {fsp: 25}\ncameras:\n  - {id: a, name: A, source: 0}",
        'cameras:\n  - {id: a, name: A, source: ""}',
    ],
)
def test_invalid_configs_rejected(tmp_path, body):
    with pytest.raises(ConfigError):
        load(write(tmp_path, body))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "nope.yaml")


def test_ffmpeg_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_PATH", "C:/ff/ffmpeg.exe")
    p = write(tmp_path, "cameras:\n  - {id: a, name: A, source: 0}")
    assert load(p).recording.ffmpeg_path == "C:/ff/ffmpeg.exe"


def test_save_is_atomic_and_validated(tmp_path):
    p = write(tmp_path, "cameras:\n  - {id: a, name: A, source: 0}")
    good = "cameras:\n  - {id: z, name: Z, source: 1}\n"
    save_config_text(p, good)
    assert p.read_text() == good
    assert not (tmp_path / "venue.yaml.tmp").exists()

    with pytest.raises(ConfigError):
        save_config_text(p, "recording: {fps: -1}")
    assert p.read_text() == good  # unchanged on rejection
    assert not (tmp_path / "venue.yaml.tmp").exists()


def test_default_ports_are_distinct(tmp_path):
    p = write(tmp_path, "cameras:\n  - {id: a, name: A, source: 0}")
    cfg = load(p)
    assert (cfg.server.port, cfg.server.public_port) == (8000, 8001)
