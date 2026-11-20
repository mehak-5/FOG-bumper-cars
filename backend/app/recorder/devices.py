"""Lists DirectShow (Windows) webcam names through ffmpeg."""

from __future__ import annotations

import asyncio
import re

_DSHOW_VIDEO = re.compile(r'"([^"]+)" \(video\)')


def parse_dshow(text: str) -> list[str]:
    """Extract unique video device names (in order) from `ffmpeg -list_devices` output."""
    return list(dict.fromkeys(_DSHOW_VIDEO.findall(text)))


async def list_webcams(ffmpeg_path: str) -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_path,
        "-hide_banner",
        "-list_devices",
        "true",
        "-f",
        "dshow",
        "-i",
        "dummy",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()  # ffmpeg prints the device list on stderr
    return parse_dshow(err.decode("utf-8", errors="replace"))
