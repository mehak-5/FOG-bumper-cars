# Prompt for Claude: Audit & Refine Bumper Car Highlight Reel System

Copy and paste the prompt below into Claude (alongside your current codebase and `system_architecture_and_spec.md`). Fill in the bracketed `[OPTIONAL CONTEXT]` section before sending.

---

```text
You are a senior systems architect and Python/TypeScript engineer helping me audit, refactor, and harden my existing **Bumper Car Highlight Reel System**.

Attached is `system_architecture_and_spec.md`, which is the single source of truth for the target architecture, end-to-end workflow, security boundaries, data contracts, and engineering standards.

## Context About My Current Codebase
- [Briefly describe what phase you are currently in, e.g., "Migrating from v1 Electron/React to Python-first" or "Phase 1-3 are built, working on Phase 4 & 6"]
- [List any known bugs, pain points, or messy areas you specifically want fixed]
- [Specify hardware/environment details if known: e.g., Windows 11, NVIDIA GPU or CPU-only, USB webcams vs RTSP, audio yes/no, 16:9 vs 9:16]

## Non-Negotiable Constraints
1. **Python-first architecture:** All application logic lives in Python (FastAPI). Keep Electron as a thin TypeScript shell exposing ONLY `desktop.quit()`. Keep UI server-rendered (Jinja2 + HTMX + Pico.css) with small, strict-mode TypeScript browser scripts compiled by `tsc` (no React, no Redux, no Vite/Webpack, no hand-written JS).
2. **Never block recording:** AI processing and reel building must run in a separate worker process and never drop frames on live FFmpeg recordings.
3. **Strict security isolation:** Operator app stays strictly on `127.0.0.1:8000`. Only the isolated Public Share App on `127.0.0.1:8001` (serving only `reel.mp4` and `poster.jpg` from `storage/public/`) may be exposed to the tunnel.
4. **Crash safety:** Enforce fragmented MP4 recording flags, clean `q` stdin shutdown for FFmpeg, atomic `session.json` writes (`tmp` -> `fsync` -> `os.replace`), and startup crash recovery.
5. **Preserve working code:** Do not rewrite working modules unnecessarily. Refactor incrementally and keep existing tests passing.

## Your Step-by-Step Workflow

### Step 1: Codebase Audit & Gap Analysis (DO NOT EDIT CODE YET)
Inspect my current repository and compare it against `system_architecture_and_spec.md`. Produce a concise gap report organized by:
1. **Critical Reliability & Security Gaps** (e.g., port 8000 exposure risks, missing HTTP Range/206 support for iOS Safari, non-atomic JSON writes, missing fragmented MP4 flags, FFmpeg killed without `q`, blocking processing on the main loop, hardcoded secrets).
2. **Architecture & Contract Drift** (e.g., EventBus vs direct module coupling, mismatched Pydantic/TypeScript WebSocket types, `venue.yaml` validation gaps, Ultralytics defaulting to BoT-SORT instead of explicit `bytetrack.yaml`, cross-fade duration shrinkage not accounted for in the 45s budget).
3. **Code Hygiene & Testing Gaps** (e.g., missing unit tests, `ruff`/`tsc --check` issues, missing `ReelPublisher` abstraction for future cloud mode).

### Step 2: Propose a Phased Refinement Plan
Break the required changes into small, independently testable PR-sized steps, prioritized strictly by safety and phase order (Recording & Crash Safety -> EventBus/Contracts -> UI & WS -> Processing & Reel Builder -> Public Share & Tunnel -> Packaging/Tests).
Wait for my approval before modifying any files.

### Step 3: Incremental Implementation & Verification
Once I approve a step:
- Implement only that step cleanly, keeping Python types (Pydantic) and TypeScript types (`messages.generated.ts`) in sync.
- Add or update `pytest` / `pytest-asyncio` tests covering the change (including fault-injection and security tests where applicable).
- Show me the exact commands to verify (`ruff check .`, `pytest`, `python tools\build_ts.py --check`, and any manual verification steps).

Start now with **Step 1: Codebase Audit & Gap Analysis**.
```