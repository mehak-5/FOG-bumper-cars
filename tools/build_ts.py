"""Compile the browser scripts (backend/app/ui/ts/*.ts -> static/js) with tsc.

Usage:
    python tools/build_ts.py           # generate message types, then compile
    python tools/build_ts.py --check   # CI: types up to date + strict type-check, writes nothing
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSCONFIG = ROOT / "backend" / "app" / "ui" / "tsconfig.json"


def find_tsc() -> list[str]:
    local = ROOT / "node_modules" / ".bin" / ("tsc.cmd" if sys.platform == "win32" else "tsc")
    if local.exists():
        return [str(local)]
    found = shutil.which("tsc")
    if found:
        return [found]
    raise SystemExit("tsc not found. Run `npm install` (typescript is a dev dependency).")


def run(cmd: list[str]) -> int:
    return subprocess.run(cmd, cwd=ROOT).returncode


def main(argv: list[str]) -> int:
    gen = [sys.executable, str(ROOT / "tools" / "gen_ts_types.py")]
    if "--check" in argv:
        return run([*gen, "--check"]) or run([*find_tsc(), "-p", str(TSCONFIG), "--noEmit"])
    return run(gen) or run([*find_tsc(), "-p", str(TSCONFIG)])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
