"""Create two synthetic videos so the app can be tested without cameras (config/venue.test.yaml)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "test" / "videos"
SOURCES = {"cam1": "testsrc2=size=640x480:rate=25", "cam2": "smptebars=size=640x480:rate=25"}


def main() -> int:
    ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, source in SOURCES.items():
        target = OUT / f"{name}.mp4"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source]
        cmd += ["-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)]
        try:
            ok = subprocess.run(cmd).returncode == 0
        except OSError:
            ok = False
        if not ok:
            print(f"Failed to create {target}. Is ffmpeg installed? (set FFMPEG_PATH if needed)")
            return 1
        print("created", target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
