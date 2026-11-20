"""Builds the standalone Python backend executable with PyInstaller.

Places the output binary at resources/backend/bumper-backend.exe where
electron/src/backend-process.ts expects it when packaged.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "resources" / "backend"
SPEC_FILE = ROOT / "bumper-backend.spec"
WORK_DIR = ROOT / "build" / "pyinstaller"


def build_backend() -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_FILE),
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(WORK_DIR),
        "--clean",
        "-y",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode == 0:
        exe = DIST_DIR / ("bumper-backend.exe" if sys.platform == "win32" else "bumper-backend")
        print(f"Successfully built backend executable: {exe}")
    else:
        print(f"Backend build failed with exit code {result.returncode}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(build_backend())
