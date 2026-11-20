"""Tests for Stage 4: Reel Builder (normalization, xfade transitions, poster, faststart)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.core.atomic import atomic_write_json
from backend.app.reel.builder import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    build_reel,
    build_xfade_filter,
    generate_poster,
    get_duration,
    join_clips,
    normalize_clip,
    probe_media,
)

has_ffmpeg = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def test_build_xfade_filter_logic() -> None:
    # 1 clip
    filter_str, out_tag, total_dur = build_xfade_filter([5.0], crossfade_sec=0.3)
    assert filter_str == ""
    assert out_tag == "0:v"
    assert total_dur == 5.0

    # 2 clips of 5.0s, crossfade 0.3s -> offset = 4.7s, total = 9.7s
    filter_str, out_tag, total_dur = build_xfade_filter([5.0, 5.0], crossfade_sec=0.3)
    assert "[0:v][1:v]xfade=transition=fade:duration=0.300:offset=4.700[v1]" in filter_str
    assert out_tag == "v1"
    assert pytest.approx(total_dur, rel=1e-3) == 9.7

    # 3 clips of 4.0s, crossfade 0.3s
    # offset 1 = 3.7s (dur 7.7s)
    # offset 2 = 7.4s (dur 11.4s)
    filter_str, out_tag, total_dur = build_xfade_filter([4.0, 4.0, 4.0], crossfade_sec=0.3)
    assert "[0:v][1:v]xfade=transition=fade:duration=0.300:offset=3.700[v1]" in filter_str
    assert "[v1][2:v]xfade=transition=fade:duration=0.300:offset=7.400[v2]" in filter_str
    assert out_tag == "v2"
    assert pytest.approx(total_dur, rel=1e-3) == 11.4


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg/ffprobe not installed")
def test_normalize_clip_and_probe(tmp_path: Path) -> None:
    # Create small synthetic test source
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )

    out_clip = tmp_path / "norm.mp4"
    normalize_clip(src, out_clip, start_sec=0.5, duration_sec=2.0)
    assert out_clip.is_file()

    info = probe_media(out_clip)
    v_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v_stream["width"] == DEFAULT_WIDTH
    assert v_stream["height"] == DEFAULT_HEIGHT
    assert v_stream["pix_fmt"] == "yuv420p"

    dur = get_duration(out_clip)
    assert pytest.approx(dur, abs=0.2) == 2.0


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg/ffprobe not installed")
def test_normalize_clip_both_cameras_hstack(tmp_path: Path) -> None:
    src1 = tmp_path / "cam1.mp4"
    src2 = tmp_path / "cam2.mp4"
    for p, color in [(src1, "red"), (src2, "blue")]:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=640x480:r=25:d=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(p),
            ],
            check=True,
        )

    out_clip = tmp_path / "hstack.mp4"
    normalize_clip(
        source_path=src1,
        out_path=out_clip,
        start_sec=0.0,
        duration_sec=2.0,
        both_cameras=True,
        second_source_path=src2,
        second_start_sec=0.0,
    )
    assert out_clip.is_file()
    info = probe_media(out_clip)
    v_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert v_stream["width"] == DEFAULT_WIDTH
    assert v_stream["height"] == DEFAULT_HEIGHT


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg/ffprobe not installed")
def test_join_clips_and_poster(tmp_path: Path) -> None:
    # Create two normalized clips
    clip1 = tmp_path / "c1.mp4"
    clip2 = tmp_path / "c2.mp4"
    for p, pattern in [
        (clip1, "testsrc2=size=1280x720:rate=25"),
        (clip2, "smptebars=size=1280x720:rate=25"),
    ]:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                pattern,
                "-t",
                "3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(p),
            ],
            check=True,
        )

    out_reel = tmp_path / "reel.mp4"
    join_clips([clip1, clip2], [3.0, 3.0], out_reel, crossfade_sec=0.3)
    assert out_reel.is_file()

    # 3.0 + 3.0 - 0.3 = 5.7s
    dur = get_duration(out_reel)
    assert pytest.approx(dur, abs=0.3) == 5.7

    poster = tmp_path / "poster.jpg"
    generate_poster(out_reel, poster, at_sec=1.0)
    assert poster.is_file()
    assert poster.stat().st_size > 500


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg/ffprobe not installed")
def test_full_reel_builder_pipeline(tmp_path: Path) -> None:
    sdir = tmp_path / "ride_20260101_100000_abc123"
    sdir.mkdir(parents=True)

    # 2 camera videos of 8 seconds
    cam1 = sdir / "camera_1.mp4"
    cam2 = sdir / "camera_2.mp4"
    for p, src in [
        (cam1, "testsrc2=size=640x480:rate=25"),
        (cam2, "smptebars=size=640x480:rate=25"),
    ]:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                src,
                "-t",
                "8",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(p),
            ],
            check=True,
        )

    # Moments: 2 selected moments (0.5s to 3.5s = 3.0s, and 4.0s to 7.0s = 3.0s)
    moments_doc = {
        "session_id": "20260101_100000_abc123",
        "total_duration_sec": 8.0,
        "selected_moments": [
            {
                "id": "m1",
                "start_sec": 0.5,
                "end_sec": 3.5,
                "duration_sec": 3.0,
                "peak_sec": 2.0,
                "best_camera_id": "camera_1",
            },
            {
                "id": "m2",
                "start_sec": 4.0,
                "end_sec": 7.0,
                "duration_sec": 3.0,
                "peak_sec": 5.5,
                "best_camera_id": "camera_2",
            },
        ],
    }
    atomic_write_json(sdir / "moments.json", moments_doc)

    meta = {
        "session_id": "20260101_100000_abc123",
        "status": "completed",
        "duration_seconds": 8.0,
        "cameras": [
            {"id": "camera_1", "file": "camera_1.mp4", "offset_sec": 0.0},
            {"id": "camera_2", "file": "camera_2.mp4", "offset_sec": 0.2},
        ],
        "reel": {
            "crossfade_seconds": 0.3,
            "both_cameras": False,
        },
    }
    atomic_write_json(sdir / "session.json", meta)

    out_reel = build_reel(sdir, meta)
    assert out_reel.is_file()
    assert (sdir / "poster.jpg").is_file()

    # Raw camera videos must still be intact
    assert cam1.is_file()
    assert cam2.is_file()

    # Temp clips cleaned up
    assert not (sdir / "clips").exists()

    # Session.json updated with reel block
    updated_meta = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    assert "reel" in updated_meta
    reel_info = updated_meta["reel"]
    assert reel_info["path"] == "reel.mp4"
    assert reel_info["poster_path"] == "poster.jpg"
    assert reel_info["width"] == 1280
    assert reel_info["height"] == 720
    assert reel_info["fps"] == 25
    assert reel_info["clip_count"] == 2
    # Duration ~ 3.0 + 3.0 - 0.3 = 5.7s (within 1.0s)
    assert 5.0 <= reel_info["duration_seconds"] <= 6.5
