# Changes Report: Commit `9161d09` to Latest (`a0f0c2d` + Working Tree)

**Project:** FOG Bumper Cars Highlight Reel System  
**Base Commit:** `9161d092ba8a19a9bd948ccdbed753f377a896b2` (*"feat: implement Phase 1 operator UI, trigger service, recorder service, and preview manager"*)  
**Target / HEAD Commit:** `a0f0c2d4a3be3705171b55f0c3daa0494ce43723` (*"feat: step 12 - thin Electron shell, remove React/Redux/Vite stack..."*)  
**Branch:** `LS-python-v1`  
**Report Date:** November 19, 2026  

---

## 1. Executive Summary

Between commit `9161d09` and the latest commit, the codebase underwent a **complete architectural overhaul** known as the **"Python-First Migration"**. 

Previously, the system operated as a full-stack Node.js/TypeScript application wrapped inside Electron, using a heavy React + Redux + Vite frontend. Over a sequence of 14 commits following a 12-step architectural refinement plan:

1. **Backend logic was fully migrated to Python (FastAPI):** Camera management, hardware triggers, session storage, crash recovery, and live streaming now run natively in Python.
2. **React, Redux, and Vite were eliminated:** Replaced with a lightweight, server-rendered **Jinja2 + HTMX + Pico.css** operator dashboard.
3. **Electron became a minimal shell:** Electron no longer bundles a web frontend or executes backend logic; its sole job is spawning the Python server and presenting the desktop window.
4. **Reliability and crash safety were hardened:** Added fragmented MP4 recording flags, atomic state writes (`tmp` -> `fsync` -> `replace`), and automated startup recovery.
5. **Net code impact:** **122 files changed**, with **6,782 additions** and **9,198 deletions** (net reduction of 2,416 lines of code), plus over 6,400 lines removed from `package-lock.json`.

---

## 2. Before vs. After Architecture

| Component | Old Architecture (`9161d09`) | New Architecture (`a0f0c2d`) |
| :--- | :--- | :--- |
| **Backend Core** | Node.js & TypeScript (`src/backend/`) | Python 3.12+ with FastAPI (`backend/app/`) |
| **Frontend UI** | React 18, Redux Toolkit, Vite, styled components | Server-rendered Jinja2 HTML, HTMX, Pico.css |
| **Electron Role** | Heavy application container bundling UI & Node services | Minimal shell ([backend-process.ts](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/electron/src/backend-process.ts)) with a single `desktop.quit()` hook |
| **Data Contracts** | TypeScript interfaces defined manually | Pydantic Python models auto-generating TypeScript via [gen_ts_types.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/tools/gen_ts_types.py) |
| **Recording Safety** | Standard MP4 output; process killed on exit | Fragmented MP4 (`frag_keyframe+empty_moov`), GOP=1, flush packets, clean `q` stdin exit |
| **State Persistence** | Direct file writes (vulnerable to corruption) | Atomic writes with `os.replace` & `os.fsync` ([atomic.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/core/atomic.py)) |
| **Test Coverage** | Limited Node test files | 14 comprehensive `pytest` suites covering crash recovery, mock cameras, APIs, and WebSockets |

---

## 3. Commit-by-Commit Changelog

The migration was executed across 14 discrete commits:

### Commit 1: `ea401e5` — Step 1: Python Backend Scaffold & Venue Config
- Created the `backend/` directory structure and `pyproject.toml`.
- Implemented [config.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/config.py) using Pydantic to validate `venue.yaml` (camera definitions, RTSP/USB URLs, ride timing, storage paths).
- Added `tests/test_config.py`.

### Commit 2: `b5daafd` — Step 2: Crash-Safe Atomic File Writer
- Implemented [atomic.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/core/atomic.py) providing `atomic_write_json`.
- Uses a temporary file, forces physical disk flush with `os.fsync`, and replaces atomically via `os.replace` to prevent corrupted JSON files during power failures.
- Added `tests/test_atomic.py`.

### Commit 3: `1db64f2` — Clean pyproject.toml
- Fixed syntax/formatting typo in `pyproject.toml`.

### Commit 4: `5168b94` — Architecture Specification
- Added [Docs/bumper_car_system_architecture_and_specification.md](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/Docs/bumper_car_system_architecture_and_specification.md), establishing the target architecture, security boundaries, and data contracts.

### Commit 5: `b3aa19f` — Step 3: Async EventBus
- Implemented [events.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/core/events.py) (`EventBus`).
- Decouples subsystems (cameras, triggers, WebSocket server) using asynchronous publish/subscribe with isolated error handling per subscriber.
- Added `tests/test_events.py`.

### Commit 6: `64f901a` — Step 4: FFmpeg Camera Recorder
- Implemented [camera.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/recorder/camera.py) (`CameraRecorder`).
- Configured FFmpeg with fragmented MP4 flags (`-movflags frag_keyframe+empty_moov+default_base_moof`).
- Implemented clean shutdown by sending `q` to FFmpeg’s stdin, with fallback to SIGTERM/SIGKILL.
- Added `tests/test_camera.py`.

### Commit 7: `3465717` — Step 5: Recorder Manager & Ride State Machine
- Implemented [recorder_manager.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/recorder/recorder_manager.py).
- Manages multi-camera lifecycles and enforces ride states: `IDLE` -> `ARMED` -> `RECORDING` -> `PROCESSING` -> `IDLE`.
- Added per-camera `offset_sec` synchronization to compensate for RTSP network latency.
- Added `tests/test_recorder_manager.py`.

### Commit 8: `bfe6915` — Step 6: Startup Recovery Service
- Implemented [startup.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/services/startup.py).
- On application launch:
  1. Detects crashed/incomplete sessions from previous runs.
  2. Resets stuck jobs.
  3. Cleans up stale temporary files.
  4. Checks available disk space and broadcasts warnings if low.
- Added `tests/test_startup.py`.

### Commit 9: `9add94b` — Step 7: Keyboard Trigger & Hardware Stub
- Implemented [keyboard_trigger.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/triggers/keyboard_trigger.py) using `pynput`.
- Features debounce protection to prevent accidental duplicate triggers.
- Added [pcb_trigger.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/triggers/pcb_trigger.py) stub for arcade/PLC hardware triggers.
- Added `tests/test_keyboard_trigger.py`.

### Commit 10: `70eb28c` — Step 8: Wire Models & TypeScript Type Generator
- Implemented [models.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/core/models.py) with Pydantic wire models for all client-server communication.
- Added [ts_generator.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/core/ts_generator.py) and [tools/gen_ts_types.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/tools/gen_ts_types.py) to generate `backend/app/ui/ts/messages.generated.ts` automatically with `--check` CI validation.
- Added `tests/test_contracts.py`.

### Commit 11: `ce33c7a` — Step 9: Camera Preview Manager
- Implemented [preview.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/recorder/preview.py).
- Spawns low-overhead FFmpeg processes producing MJPEG streams over pipes for operator preview.
- Includes automatic exponential backoff reconnection and seamless hand-off when full recording starts.
- Added `tests/test_preview.py`.

### Commit 12: `37c2235` — Step 10: FastAPI Operator Application
- Implemented [main.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/main.py) app factory and lifespan management.
- Implemented [routes.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/api/routes.py) for REST control (arm, trigger, cancel, stop, camera status, logs).
- Implemented [router.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/ws/router.py) and [connection_manager.py](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/backend/app/ws/connection_manager.py) for WebSocket broadcasting.
- Enforced security guards restricting access to `127.0.0.1` and origin checking.
- Added `tests/test_api.py` and `tests/test_ws.py`.

### Commit 13: `c7ccc59` — Step 11: Jinja2 / HTMX / Pico Operator Dashboard
- Implemented server-rendered UI in `backend/app/ui/`:
  - Templates: `base.html`, `console.html`, `cameras.html`, `history.html`, `logs.html`, `settings.html`.
  - Static vendor assets: `htmx.min.js`, `pico.min.css`.
  - Type-safe browser scripts: `console.ts`, `ws-client.ts`, `logs.ts`, `desktop.ts`.
- Added `tests/test_ui.py`.

### Commit 14: `a0f0c2d` — Step 12: Thin Electron Shell, Stack Deletion & Hardening
- **Deleted old stack:** Removed `src/frontend/`, `src/backend/`, `vite.*.config.mts`, `forge.config.mts`, and thousands of unused node modules.
- **Thin Electron Shell:** Implemented [backend-process.ts](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/electron/src/backend-process.ts) and [main.ts](file:///d:/FOG%20Tech/Projects/FOG-bumper-cars/electron/src/main.ts) in `electron/src/`. Electron merely manages the Python child process and opens the browser window.
- **Recording Resilience:** Set `-g 1` (GOP size = 1, every frame is an IDR-frame) and `-flush_packets 1` so that a sudden system kill leaves a fully playable MP4 up to the exact final frame.
- Added `tests/test_integration_ffmpeg.py` and Electron unit tests (`electron/test/backend-process.test.mjs`).

---

## 4. Current Work in Progress (Uncommitted Working Tree)

In your current working directory, development is underway on **Phase 4: AI Highlight Reel Processing**:

- **`backend/app/processing/` (New Subsystem):**
  - `pipeline.py`: Defines the processing pipeline stages.
  - `service.py`: Orchestrates job queuing, session tracking, and status events.
  - `worker.py`: Dedicated background worker process that handles AI inference (YOLO detection / ByteTrack tracking) without degrading frame rates on live camera recordings.
- **`backend/app/main.py` & `context.py`:**
  - Added hooks to start and cleanly stop the `ProcessingService`.
  - Connected `Events.PROCESSING_PROGRESS` and `Events.REEL_READY` to the WebSocket broadcaster.

---

## 5. Key Improvements Explained in Simple Terms

1. **Why Fragmented MP4 + GOP 1 Matters:**
   Standard video files store their index header ("moov atom") at the end of recording. If the power cable is pulled or the app crashes, that header is never written, making the entire video corrupt and unreadable. With fragmented MP4 and GOP 1, every individual frame is self-contained. The video file is 100% playable right up to the millisecond the power was cut.

2. **Why Moving Off React/Redux Was Beneficial:**
   The operator console only needs to show camera previews, ride status, and basic buttons. Using React + Redux + Webpack/Vite introduced high memory usage, slow build steps, and complex IPC bridging. Switching to HTMX and Pico.css reduced frontend bundle size to near zero and allowed Python to serve the entire app directly.

3. **Single Source of Truth for Data:**
   Instead of updating TypeScript types in one place and backend models in another, Python Pydantic models define everything. The build tool automatically updates the TypeScript types, eliminating desynchronization bugs.
