"""Print webcam names (Windows DirectShow) to copy into config/venue.yaml."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.recorder.devices import list_webcams  # noqa: E402


async def main() -> int:
    ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
    try:
        names = await list_webcams(ffmpeg)
    except OSError as err:
        print(f"Cannot run ffmpeg ('{ffmpeg}'): {err}")
        return 1
    if not names:
        print("No webcams found.")
    for i, name in enumerate(names):
        print(f'#{i}  "{name}"')
    print("\nUse the index (source: 0) or the exact name in config/venue.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
