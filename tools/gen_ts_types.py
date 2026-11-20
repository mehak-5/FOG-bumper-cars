"""Generate backend/app/ui/ts/messages.generated.ts from the Pydantic wire models.

Usage:
    python tools/gen_ts_types.py           # write the file
    python tools/gen_ts_types.py --check   # exit 1 if the committed file is stale (CI)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.core.ts_generator import generate  # noqa: E402

OUT = ROOT / "backend" / "app" / "ui" / "ts" / "messages.generated.ts"


def main(argv: list[str]) -> int:
    text = generate()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{OUT.relative_to(ROOT)} is out of date - run: python tools/gen_ts_types.py")
            return 1
        print("messages.generated.ts is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
