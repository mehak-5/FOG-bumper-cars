"""Small OS integrations for the operator PC."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


async def open_in_file_manager(path: Path) -> None:
    """Show a folder in Explorer/Finder. Only ever called with an already-validated path."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    await asyncio.create_subprocess_exec(opener, str(path))
