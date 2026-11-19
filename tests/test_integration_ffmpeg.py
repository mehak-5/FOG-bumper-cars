"""Real ffmpeg, no fakes. Skipped automatically when ffmpeg/ffprobe are not installed."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess

import pytest

from backend.app.config import (
    AppConfig,
    CameraConfig,
    FileSource,
    RecordingConfig,
    ServerConfig,
    TriggerConfig,
    VenueInfo,
)
from backend.app.core.events import EventBus
from backend.app.recorder.recorder_manager import RecorderManager

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


def probe(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def source_videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    paths = []
    for name, src in (
        ("a", "testsrc2=size=640x480:rate=25"),
        ("b", "smptebars=size=640x480:rate=25"),
    ):
        p = d / f"{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", "6",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)],
            check=True,
        )  # fmt: skip
        paths.append(p)
    return paths


def manager(tmp_path, sources):
    cfg = AppConfig(
        venue=VenueInfo(name="it"),
        recording=RecordingConfig(open_timeout_seconds=15, disconnect_timeout_seconds=5),
        trigger=TriggerConfig(),
        server=ServerConfig(),
        sessions_dir=tmp_path / "sessions",
        cameras=[
            CameraConfig(id=f"camera_{i}", name=f"Cam {i}", source=FileSource(path=str(p)))
            for i, p in enumerate(sources, 1)
        ],
        disk_warn_gb=0,
    )
    return RecorderManager(cfg, EventBus())


def session_dir(mgr):
    (d,) = list(mgr.cfg.sessions_dir.iterdir())
    return d


async def test_real_ride_produces_playable_fragmented_mp4s(tmp_path, source_videos):
    mgr = manager(tmp_path, source_videos)
    await mgr.start_ride()
    assert mgr.ride == "recording", mgr.get_state()["cameras"]
    await asyncio.sleep(3)
    await mgr.end_ride({"reason": "trigger"})

    d = session_dir(mgr)
    meta = json.loads((d / "session.json").read_text())
    assert meta["status"] == "completed"
    for cam in meta["cameras"]:
        assert cam["status"] == "completed" and cam["frames"] > 25
        assert cam["offset_sec"] is not None and 0 <= cam["offset_sec"] < 10
        video = d / cam["file"]
        info = probe(video)
        stream = info["streams"][0]
        assert (stream["codec_name"], stream["width"], stream["height"]) == ("h264", 1280, 720)
        assert float(info["format"]["duration"]) >= 2.0
        assert b"moof" in video.read_bytes()  # fragmented MP4: survives a crash


async def test_hard_kill_mid_ride_leaves_playable_files(tmp_path, source_videos):
    mgr = manager(tmp_path, source_videos)
    await mgr.start_ride()
    assert mgr.ride == "recording"
    await asyncio.sleep(3)
    mgr.kill_all()  # simulates ffmpeg being killed / power loss: no clean `q` shutdown
    for _ in range(100):
        if mgr.ride == "idle":
            break
        await asyncio.sleep(0.1)

    d = session_dir(mgr)
    videos = sorted(d.glob("*.mp4"))
    assert videos, "recordings must survive a hard kill"
    for v in videos:
        assert float(probe(v)["format"]["duration"]) > 0.5  # still playable thanks to fragments
    assert json.loads((d / "session.json").read_text())["status"] in ("failed", "partial")
