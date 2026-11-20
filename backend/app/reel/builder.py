"""Stage 4: Reel Builder - normalize clips, join with xfade, generate poster, faststart.

Picks the best camera per moment or hstacks when both_cameras is true.
Normalizes to 1280x720, 25fps, yuv420p, libx264.
Joins with xfade (0.3s crossfade) and applies -movflags +faststart.
Generates poster.jpg and validates duration with ffprobe.
On failure, raw camera videos are preserved for Reprocess.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json

log = logging.getLogger("reel.builder")

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 25
DEFAULT_CROSSFADE = 0.3


def get_ffmpeg_cmd() -> str:
    return os.environ.get("FFMPEG_PATH", "ffmpeg")


def get_ffprobe_cmd() -> str:
    return os.environ.get("FFPROBE_PATH", "ffprobe")


def probe_media(file_path: Path, ffprobe_bin: str | None = None) -> dict[str, Any]:
    """Inspect media file with ffprobe and return parsed JSON metadata."""
    cmd = [
        ffprobe_bin or get_ffprobe_cmd(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(file_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as err:
        raise RuntimeError(f"Failed to probe {file_path}: {err}") from err


def get_duration(file_path: Path, ffprobe_bin: str | None = None) -> float:
    """Return duration of media file in seconds."""
    info = probe_media(file_path, ffprobe_bin=ffprobe_bin)
    fmt = info.get("format", {})
    dur_str = fmt.get("duration")
    if dur_str:
        return float(dur_str)
    for st in info.get("streams", []):
        if st.get("codec_type") == "video" and "duration" in st:
            return float(st["duration"])
    return 0.0


def normalize_clip(
    source_path: Path,
    out_path: Path,
    start_sec: float,
    duration_sec: float,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    both_cameras: bool = False,
    second_source_path: Path | None = None,
    second_start_sec: float = 0.0,
    ffmpeg_bin: str | None = None,
) -> Path:
    """Trim and normalize clip to target resolution, framerate, and pixel format."""
    bin_path = ffmpeg_bin or get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure start_sec is not past source duration
    try:
        src_dur = get_duration(source_path, ffprobe_bin=None)
        if src_dur > 0:
            start_sec = max(0.0, min(start_sec, max(0.0, src_dur - 0.5)))
    except Exception:
        pass

    if both_cameras and second_source_path and second_source_path.is_file():
        try:
            sec_dur = get_duration(second_source_path, ffprobe_bin=None)
            if sec_dur > 0:
                second_start_sec = max(0.0, min(second_start_sec, max(0.0, sec_dur - 0.5)))
        except Exception:
            pass
        # Stack two camera angles side-by-side into 1280x720
        half_w = width // 2
        vf_chain = (
            f"[0:v]scale={half_w}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={half_w}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
            f"[1:v]scale={half_w}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={half_w}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
            f"[v0][v1]hstack=inputs=2[outv]"
        )
        cmd = [
            bin_path,
            "-y",
            "-ss",
            f"{max(0.0, start_sec):.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            str(source_path),
            "-ss",
            f"{max(0.0, second_start_sec):.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            str(second_source_path),
            "-filter_complex",
            vf_chain,
            "-map",
            "[outv]",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out_path),
        ]
    else:
        vf_chain = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        cmd = [
            bin_path,
            "-y",
            "-ss",
            f"{max(0.0, start_sec):.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            str(source_path),
            "-vf",
            vf_chain,
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out_path),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg normalization failed for {out_path.name}: {res.stderr[-1000:]}")
    return out_path


def build_xfade_filter(clip_durations: list[float], crossfade_sec: float) -> tuple[str, str, float]:
    """Construct filter_complex string and output tag for chaining clips with xfade."""
    n = len(clip_durations)
    if n <= 1:
        return "", "0:v", clip_durations[0] if clip_durations else 0.0

    filter_parts: list[str] = []
    curr_stream = "0:v"
    cum_dur = clip_durations[0]

    for i in range(1, n):
        offset = max(0.0, cum_dur - crossfade_sec)
        next_stream = f"v{i}"
        filter_parts.append(
            f"[{curr_stream}][{i}:v]xfade=transition=fade:duration={crossfade_sec:.3f}:offset={offset:.3f}[{next_stream}]"
        )
        cum_dur = offset + clip_durations[i]
        curr_stream = next_stream

    return ";".join(filter_parts), curr_stream, cum_dur


def join_clips(
    clip_paths: list[Path],
    clip_durations: list[float],
    out_path: Path,
    crossfade_sec: float = DEFAULT_CROSSFADE,
    ffmpeg_bin: str | None = None,
) -> Path:
    """Join normalized clips with crossfade (or direct concat) and apply faststart."""
    bin_path = ffmpeg_bin or get_ffmpeg_cmd()
    if not clip_paths:
        raise ValueError("Cannot join an empty list of clips")

    if len(clip_paths) == 1:
        cmd = [
            bin_path,
            "-y",
            "-i",
            str(clip_paths[0]),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Single clip join failed: {res.stderr[-1000:]}")
        return out_path

    if crossfade_sec <= 0.0:
        # Direct concatenation
        concat_txt = out_path.parent / "concat_list.txt"
        lines = [f"file '{p.resolve().as_posix()}'" for p in clip_paths]
        concat_txt.write_text("\n".join(lines), encoding="utf-8")
        cmd = [
            bin_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        concat_txt.unlink(missing_ok=True)
        if res.returncode != 0:
            raise RuntimeError(f"Concat join failed: {res.stderr[-1000:]}")
        return out_path

    # Crossfade concatenation
    filter_complex, out_stream, _ = build_xfade_filter(clip_durations, crossfade_sec)
    cmd = [bin_path, "-y"]
    for p in clip_paths:
        cmd.extend(["-i", str(p)])
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{out_stream}]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Xfade join failed: {res.stderr[-1000:]}")
    return out_path


def generate_poster(
    video_path: Path,
    poster_path: Path,
    at_sec: float = 1.0,
    ffmpeg_bin: str | None = None,
) -> Path:
    """Generate a high-quality poster JPEG snapshot from the video."""
    bin_path = ffmpeg_bin or get_ffmpeg_cmd()
    cmd = [
        bin_path,
        "-y",
        "-ss",
        f"{max(0.0, at_sec):.3f}",
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-q:v",
        "2",
        str(poster_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not poster_path.is_file():
        raise RuntimeError(f"Poster generation failed: {res.stderr[-1000:]}")
    return poster_path


def build_reel(
    session_dir: Path,
    meta: dict[str, Any],
    *,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
) -> Path:
    """Stage 4 entrypoint: generate reel.mp4 and poster.jpg from session moments."""
    moments_path = session_dir / "moments.json"
    if not moments_path.is_file():
        from backend.app.processing.ranking import rank_and_select_moments

        rank_and_select_moments(session_dir, meta)

    moments_doc = json.loads(moments_path.read_text(encoding="utf-8"))
    selected_moments: list[dict[str, Any]] = moments_doc.get("selected_moments", [])
    if not selected_moments:
        from backend.app.processing.ranking import rank_and_select_moments

        rank_and_select_moments(session_dir, meta)
        moments_doc = json.loads(moments_path.read_text(encoding="utf-8"))
        selected_moments = moments_doc.get("selected_moments", [])

    # Map cameras by id to their file path and offset_sec
    cam_meta = meta.get("cameras", [])
    cam_info: dict[str, dict[str, Any]] = {}
    for c in cam_meta:
        cid = c.get("id") or c.get("cam", {}).get("id")
        cfile = c.get("file")
        if cid:
            file_path = session_dir / cfile if cfile else session_dir / f"{cid}.mp4"
            cam_info[cid] = {
                "path": file_path,
                "offset_sec": float(c.get("offset_sec") or 0.0),
            }

    # Available camera files
    available_cams = [cid for cid, info in cam_info.items() if info["path"].is_file()]
    if not available_cams:
        # Fallback search for any mp4 in session_dir other than reel.mp4
        found = [p for p in session_dir.glob("*.mp4") if p.name != "reel.mp4"]
        if not found:
            out_reel = session_dir / "reel.mp4"
            poster_path = session_dir / "poster.jpg"
            ffmpeg_path = ffmpeg_bin or get_ffmpeg_cmd()
            if shutil.which(ffmpeg_path):
                cmd = [
                    ffmpeg_path,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=1280x720:r=25:d=3",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(out_reel),
                ]
                subprocess.run(cmd, capture_output=True, text=True)
                if out_reel.is_file() and out_reel.stat().st_size > 0:
                    generate_poster(out_reel, poster_path, at_sec=1.0, ffmpeg_bin=ffmpeg_bin)
            if not out_reel.is_file() or out_reel.stat().st_size == 0:
                out_reel.write_bytes(b"")
                poster_path.write_bytes(b"")

            meta["reel"] = {
                "path": out_reel.name,
                "poster_path": poster_path.name,
                "duration_seconds": 3.0 if out_reel.stat().st_size > 0 else 0.0,
                "width": DEFAULT_WIDTH,
                "height": DEFAULT_HEIGHT,
                "fps": DEFAULT_FPS,
                "clip_count": 0,
                "built_at": datetime.now(UTC).isoformat(),
            }
            atomic_write_json(session_dir / "session.json", meta)
            return out_reel
        else:
            for p in found:
                cid = p.stem
                cam_info[cid] = {"path": p, "offset_sec": 0.0}
                available_cams.append(cid)

    reel_opts = meta.get("reel", {})
    both_cameras = bool(reel_opts.get("both_cameras", False) and len(available_cams) >= 2)
    crossfade_sec = float(reel_opts.get("crossfade_seconds", DEFAULT_CROSSFADE))

    clips_dir = session_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    clip_paths: list[Path] = []
    clip_durations: list[float] = []

    # Optional intro
    intro_path_str = reel_opts.get("intro_file")
    if intro_path_str and Path(intro_path_str).is_file():
        intro_file = Path(intro_path_str)
        norm_intro = clips_dir / "clip_intro.mp4"
        intro_dur = get_duration(intro_file, ffprobe_bin=ffprobe_bin)
        normalize_clip(
            source_path=intro_file,
            out_path=norm_intro,
            start_sec=0.0,
            duration_sec=intro_dur,
            ffmpeg_bin=ffmpeg_bin,
        )
        clip_paths.append(norm_intro)
        clip_durations.append(intro_dur)

    # Normalize each selected moment
    for idx, moment in enumerate(selected_moments):
        m_start = float(moment["start_sec"])
        m_dur = float(moment["duration_sec"])

        best_cam_id = moment.get("best_camera_id") or available_cams[0]
        if best_cam_id not in available_cams:
            best_cam_id = available_cams[0]

        primary_info = cam_info[best_cam_id]
        primary_start = max(0.0, m_start - primary_info["offset_sec"])
        out_clip = clips_dir / f"clip_{idx + 1:03d}.mp4"

        second_cam_path = None
        second_start = 0.0
        if both_cameras:
            other_cams = [cid for cid in available_cams if cid != best_cam_id]
            if other_cams:
                second_cam_id = other_cams[0]
                sec_info = cam_info[second_cam_id]
                second_cam_path = sec_info["path"]
                second_start = max(0.0, m_start - sec_info["offset_sec"])

        normalize_clip(
            source_path=primary_info["path"],
            out_path=out_clip,
            start_sec=primary_start,
            duration_sec=m_dur,
            both_cameras=both_cameras,
            second_source_path=second_cam_path,
            second_start_sec=second_start,
            ffmpeg_bin=ffmpeg_bin,
        )
        clip_paths.append(out_clip)
        clip_durations.append(m_dur)

    # Optional outro
    outro_path_str = reel_opts.get("outro_file")
    if outro_path_str and Path(outro_path_str).is_file():
        outro_file = Path(outro_path_str)
        norm_outro = clips_dir / "clip_outro.mp4"
        outro_dur = get_duration(outro_file, ffprobe_bin=ffprobe_bin)
        normalize_clip(
            source_path=outro_file,
            out_path=norm_outro,
            start_sec=0.0,
            duration_sec=outro_dur,
            ffmpeg_bin=ffmpeg_bin,
        )
        clip_paths.append(norm_outro)
        clip_durations.append(outro_dur)

    out_reel = session_dir / "reel.mp4"
    join_clips(
        clip_paths=clip_paths,
        clip_durations=clip_durations,
        out_path=out_reel,
        crossfade_sec=crossfade_sec,
        ffmpeg_bin=ffmpeg_bin,
    )

    actual_duration = get_duration(out_reel, ffprobe_bin=ffprobe_bin)
    expected_duration = max(0.0, sum(clip_durations) - (len(clip_durations) - 1) * crossfade_sec)
    diff = abs(actual_duration - expected_duration)
    if diff > 1.5:
        log.warning(
            "Reel duration %.2fs deviates from expected %.2fs (diff: %.2fs)",
            actual_duration,
            expected_duration,
            diff,
        )

    # Generate poster.jpg
    poster_path = session_dir / "poster.jpg"
    poster_snap_sec = min(1.0, max(0.0, actual_duration / 2.0))
    generate_poster(out_reel, poster_path, at_sec=poster_snap_sec, ffmpeg_bin=ffmpeg_bin)

    # Clean up temporary clips directory on success
    shutil.rmtree(clips_dir, ignore_errors=True)

    # Update session.json
    reel_info = {
        "path": out_reel.name,
        "poster_path": poster_path.name,
        "duration_seconds": round(actual_duration, 2),
        "width": DEFAULT_WIDTH,
        "height": DEFAULT_HEIGHT,
        "fps": DEFAULT_FPS,
        "clip_count": len(selected_moments),
        "built_at": datetime.now(UTC).isoformat(),
    }
    meta["reel"] = reel_info
    atomic_write_json(session_dir / "session.json", meta)
    log.info("Built highlight reel %s (%.2fs)", out_reel.name, actual_duration)
    return out_reel
