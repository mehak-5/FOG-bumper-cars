"""Local runner for all CI checks.

Runs in order:
1. ruff check .
2. ruff format --check backend tests tools
3. python -m pytest
4. python tools/build_ts.py --check
5. npm run typecheck
6. npm test
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECKS: list[tuple[str, list[str]]] = [
    ("Ruff Lint", ["ruff", "check", "."]),
    ("Ruff Format", ["ruff", "format", "--check", "backend", "tests", "tools"]),
    ("Pytest Suite", [sys.executable, "-m", "pytest"]),
    ("TypeScript Codegen Check", [sys.executable, "tools/build_ts.py", "--check"]),
    ("TypeScript Typecheck", ["npm.cmd" if sys.platform == "win32" else "npm", "run", "typecheck"]),
    ("Electron Tests", ["npm.cmd" if sys.platform == "win32" else "npm", "test"]),
]


def run_checks() -> int:
    failed = []
    for name, cmd in CHECKS:
        print(f"\n{'=' * 20} Running: {name} {'=' * 20}")
        print(f"Command: {' '.join(cmd)}")
        res = subprocess.run(cmd, cwd=str(ROOT))
        if res.returncode != 0:
            print(f"FAILED: {name} (exit code {res.returncode})")
            failed.append(name)
        else:
            print(f"PASSED: {name}")

    print("\n" + "=" * 50)
    if failed:
        print(f"CI checks FAILED ({len(failed)} failures): {', '.join(failed)}")
        return 1
    print("All CI checks PASSED cleanly!")
    return 0


if __name__ == "__main__":
    sys.exit(run_checks())
