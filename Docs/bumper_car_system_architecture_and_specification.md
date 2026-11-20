# Bumper Car Highlight Reel System — Master Architecture & Revision Specification

## 1. Final Goal
Build a reliable, automated desktop application for bumper-car attractions that:
1. Records each ride from **two cameras in sync** via a simple operator trigger (keyboard `S`/`E`/`Q`, on-screen UI button, or later a physical PCB button).
2. Automatically analyzes the recorded footage in the background using **YOLO + ByteTrack** to detect high-action moments (collisions, near-misses, high speed, crowding).
3. Ranks and selects the best moments (targeting ~45 seconds) and stitches them into a single, phone-ready highlight video (`reel.mp4`) using **FFmpeg**.
4. Publishes each highlight reel behind an isolated, token-protected public web page accessible via a secure tunnel (**Cloudflare Tunnel** or **ngrok**) and **QR code** (with optional cloud upload in a later phase).

### Non-Negotiable System Rules
* **Recording is sacred:** AI processing, reel building, or tunnel issues must *never* interrupt, delay, or drop frames from a live recording.
* **Strict network isolation:** The Operator App (`127.0.0.1:8000`) is strictly local and never exposed to the internet. Only the Public Share App (`127.0.0.1:8001`) is reachable through the tunnel.
* **Offline resilience:** Rides record and reels build locally even if the internet or tunnel is down.
* **Crash safety:** Power loss or unexpected shutdowns must leave recordings playable (fragmented MP4), metadata intact (atomic JSON writes), and interrupted sessions automatically detected and flagged on restart.

---

## 2. Technology Stack & Language Policy

The system follows a **Python-first** architecture. There is **no React, no Redux, no Node backend, no hand-written JavaScript, and no frontend bundler** (only `tsc`).

| Layer | Technology | Responsibility |
| :--- | :--- | :--- |
| **Backend & All Logic** | Python 3.11+, FastAPI, Uvicorn, Pydantic, PyYAML | Recording, triggers, EventBus, AI processing, reel building, session storage, logging, tunnel management, REST/WebSocket APIs, and serving UI HTML. |
| **Operator UI** | Jinja2 templates, HTMX, Pico.css (vendored offline) | Server-rendered pages with partial updates and zero heavy frontend framework. |
| **Browser Scripts** | TypeScript (`backend/app/ui/ts/*.ts` $\rightarrow$ `static/js/`) | WebSocket client with auto-reconnect, live preview rendering, and live log streaming. Compiled via `tsc` as native ES modules (`.js` imports). |
| **Desktop Shell** | Electron + TypeScript (`electron/src/*.ts` $\rightarrow$ `dist/`) | Thin desktop wrapper: single-instance lock, spawn FastAPI backend, poll `/api/health`, open window to `http://127.0.0.1:8000/`, expose only `desktop.quit()` via preload. |
| **Video & AI** | FFmpeg (system binary), Ultralytics YOLO, ByteTrack, OpenCV, Pandas, PyArrow | DirectShow/RTSP capture, JPEG previews, frame extraction, object tracking, trimming, normalization, and cross-fade stitching. |
| **Public Sharing** | `cloudflared` (or `ngrok`), `qrcode[pil]` | Outbound HTTPS tunnel to port `8001` only, plus QR code generation. |
| **Quality & Packaging** | `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `PyInstaller`, `electron-builder` | Automated testing, linting, type-checking, and Windows installer packaging. |

*Note: WebSocket message types are generated from Python Pydantic models (`backend/app/core/models.py`) into `backend/app/ui/ts/messages.generated.ts` via `tools/gen_ts_types.py` so backend and frontend types never drift.*

---

## 3. System Architecture

### Two-Zone Process Layout
1. **Zone 1 — Operator PC (Private, `127.0.0.1:8000`):**
   * **Electron Main Process:** Manages the desktop window and backend process lifecycle.
   * **FastAPI Operator App:** Hosts UI routes (`/`, `/cameras`, `/settings`, `/history`, `/logs`), REST API (`/api/*`), and WebSocket (`/ws`).
   * **Internal Async EventBus (`core/events.py`):** Decoupled pub/sub hub. Modules communicate strictly via events; handlers are isolated so one failure cannot break others.
   * **FFmpeg Child Processes:** One dedicated FFmpeg process per enabled camera during recording.
   * **Processing Worker Process:** Runs in a separate OS process, one job at a time, reading saved files from disk.
2. **Zone 2 — Public Share Zone (`127.0.0.1:8001`, Tunnel Target Only):**
   * **Separate FastAPI Share App:** Runs on `public_port` (`8001`). Startup must refuse to launch the tunnel if `public_port == port`.
   * **Hardened Isolation:** OpenAPI/docs endpoints disabled (`docs_url=None`, `redoc_url=None`, `openapi_url=None`). Serves *only* `reel.mp4` and `poster.jpg` copied/hard-linked into `storage/public/<session_id>/`—never raw videos or `session.json`.
   * **Tunnel Client (`cloudflared` / `ngrok`):** Points exclusively to `http://127.0.0.1:8001`.

### Target Directory Layout
```text
bumper-car/
├── config/
│   ├── venue.yaml                  # Live config (validated by Pydantic)
│   └── venue.test.yaml             # Hardware-free test config
├── electron/                       # Thin TypeScript desktop shell
│   └── src/                        # main.ts, backend-process.ts, preload.ts
├── backend/
│   ├── models/                     # YOLO weights (not committed to git)
│   └── app/
│       ├── main.py                 # App factory, lifespan, EventBus wiring
│       ├── config.py               # venue.yaml Pydantic loader
│       ├── constants.py            # Enums, message types
│       ├── core/                   # events.py, models.py, logger.py
│       ├── ws/                     # router.py, connection_manager.py, handlers.py
│       ├── api/                    # health.py, devices.py, config_routes.py, sessions.py
│       ├── ui/                     # router.py, templates/, ts/, static/
│       ├── public/                 # Isolated port-8001 FastAPI app & reel.html
│       ├── recorder/               # camera.py, recorder_manager.py, preview.py, devices.py
│       ├── triggers/               # keyboard_trigger.py (pynput), pcb_trigger.py (stub)
│       ├── services/               # startup.py, session_store.py, share.py, tunnel.py, qr.py
│       ├── processing/             # worker.py, pipeline.py, extraction.py, segmentation.py, ranking.py
│       └── reel/                   # builder.py
├── storage/
│   ├── sessions/                   # One ride_<YYYYMMDD_HHMMSS>_<hex>/ folder per ride
│   ├── public/                     # Hard-linked/copied reel.mp4 + poster.jpg only
│   ├── share_index.json            # SHA-256 token hash -> session_id, expiry, revoked
│   └── logs/                       # Rotating log files
├── tests/                          # Unit, API, WS, pipeline, and security tests
└── tools/                          # dev.py, build_ts.py, gen_ts_types.py, make_test_videos.py, etc.
```

---

## 4. End-to-End Workflow

### Stage 1: Startup & Readiness
* `StartupService` runs on launch: marks any leftover `recording` sessions as `interrupted`, resets stuck processing jobs back to `pending`, cleans `.tmp` files, and checks free disk space (warns below `5 GB`).
* `PreviewManager` streams ~2 FPS JPEG thumbnails per camera over WebSocket.
* **Readiness Guard:** `Start` is disabled until every enabled camera delivers a fresh frame within 2 seconds.

### Stage 2: Trigger & Ride State Machine
* **State Machine:** `IDLE` $\rightarrow$ `STARTING` $\rightarrow$ `RECORDING` $\rightarrow$ `STOPPING` $\rightarrow$ `IDLE`.
* **Triggers:** Keyboard (`S` start, `E` end, `Q` quit via `pynput`), UI buttons (WebSocket), or PCB button (Phase 5). All triggers obey `debounce_seconds: 0.5` and emit `ride.start` / `ride.end` with `{ source }`.
* **Recording:**
  1. Pause previews (DirectShow cameras cannot be opened by two readers simultaneously; UI freezes last frame and shows a live recording indicator driven by file-size growth).
  2. Spawn one FFmpeg process per camera using crash-safe flags (`-rtbufsize 256M`, `-use_wallclock_as_timestamps 1`, `-movflags +frag_keyframe+empty_moov`, `libx264`, `1280x720`, `25 fps`).
  3. Record each camera's `offset_sec` from wall-clock start time for timeline alignment.
  4. If one camera disconnects mid-ride, mark it `failed`, keep recording the other, and finish the ride as `partial`.
* **Stopping:** Triggered by `E`/UI, auto-stop timer, or hard safety cap (`max_duration_seconds: 600`). Send `q` to FFmpeg `stdin` and wait ~5s before force-killing. Delete 0-byte files, write `session.json` atomically (`tmp` $\rightarrow$ `flush` $\rightarrow$ `fsync` $\rightarrow$ `os.replace`), resume previews, publish `ride.finished` and `processing.queued`.

### Stage 3: Background AI Processing (Separate Worker Process)
Status flow: `pending` $\rightarrow$ `extracting` $\rightarrow$ `segmenting` $\rightarrow$ `ranking` $\rightarrow$ `building` $\rightarrow$ `done` (or `failed` with stage + stderr saved for retry).

1. **Extraction (`extraction.py`):**
   * Sample every `frame_stride` frames (default `3`) from each camera video.
   * Run Ultralytics YOLO with **explicit ByteTrack** (`tracker="bytetrack.yaml"`, `conf=0.35`, `persist=True`).
   * Align timestamps to ride time using `offset_sec` and save `detections.parquet`.
   * *Requirement:* Plan to fine-tune weights on 300–1,000 labeled venue frames (`bumper_car`, `person`) since stock COCO models poorly detect bumper cars.
2. **Segmentation (`segmentation.py`):**
   * Merge both cameras onto a shared timeline (1-second bins).
   * Compute per-window features: `object_count`, `speed`, `accel` (sudden drops = impacts), `iou_max` (box overlap), and `proximity_time`.
   * Combine normalized features into an activity signal, smooth it, and detect peaks.
   * Pad peaks (`pre_pad_seconds: 1.5`, `post_pad_seconds: 1.5`), merge overlaps, clamp to `2–8 s`, classify `event_type` (`collision_candidate`, `near_miss`, `high_speed`, `crowd`), and save `moments.json`.
3. **Ranking & Selection (`ranking.py`):**
   * Score each candidate moment (all components scaled $0$ to $1$):
     $$S = w_e \cdot E + w_d \cdot D + w_m \cdot M + w_a \cdot A + w_c \cdot C$$
     Default weights: $w_e = 0.35$ (event), $w_m = 0.25$ (motion), $w_d = 0.15$ (density), $w_a = 0.15$ (camera agreement), $w_c = 0.10$ (confidence).
   * Use a max-heap (`heapq` with negative scores) to greedily pick moments until `target_reel_seconds: 45` (accounting for cross-fade duration loss).
   * Enforce `min_gap_seconds: 3` and `max_per_event_type: 3`. If a ride yields fewer than a minimum floor (e.g., 15s), fall back to evenly spaced clips. Sort selected moments chronologically.

### Stage 4: Reel Builder (`reel/builder.py`)
1. Pick the higher-scoring camera per moment (avoiding rapid back-and-forth camera flipping) or stack side-by-side (`hstack`) if `reel.both_cameras: true`, applying `offset_sec`.
2. Trim and normalize each clip individually (`1280x720`, `25 fps`, `yuv420p`, `libx264`) into a temp `clips/` folder for easy debugging.
3. Join clips with FFmpeg `xfade` (`crossfade_seconds: 0.3`, or `concat` demuxer with `-c copy` if `0`), optionally prepending/appending normalized `intro_file` / `outro_file`, and apply `-movflags +faststart` for instant mobile playback.
4. Generate `poster.jpg` for preview, verify `reel.mp4` duration via `ffprobe`, update `session.json` atomically, clean up temp `clips/`, and publish `reel.ready`.

### Stage 5: Public Sharing, Tunnel & QR
1. On `reel.ready`, `ShareService` copies/hard-links `reel.mp4` and `poster.jpg` to `storage/public/<session_id>/`.
2. Generates a cryptographically random URL-safe token (128–192 bits via `secrets.token_urlsafe`), stores only its **SHA-256 hash** in `storage/share_index.json` with `expires_at` (default `72 h`), writes the share block into `session.json`, and publishes `share.created`.
3. **Public Routes (`127.0.0.1:8001` only):**
   * `GET /healthz` — Tunnel health check (`ok`).
   * `GET /r/{token}` — Minimal HTML video player + optional download button.
   * `GET /r/{token}/video` — Streams `reel.mp4` with **HTTP Range (`206 Partial Content`)** support (mandatory for iOS Safari).
   * `GET /r/{token}/poster` — Serves `poster.jpg`.
   * `GET /r/{token}/download` — Downloads `reel.mp4` (`Content-Disposition: attachment`).
4. **Security Hardening:** Unknown, expired, and revoked tokens all return the exact same generic `404` page. Apply per-IP rate limiting (reading `CF-Connecting-IP` behind Cloudflare), `Cache-Control: private, no-store`, `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`, and no CORS/cookies/WebSockets.
5. **Tunnel Manager:** Runs `cloudflared` (named tunnel with fixed domain for production QR codes; quick tunnel for dev only), polls `{public_url}/healthz` every 30s, auto-restarts on failure, and masks secrets in logs.
6. **Future Cloud Abstraction:** Implement sharing behind a `ReelPublisher` protocol (`TunnelPublisher` now, `CloudPublisher` for S3/GCS presigned URLs in Phase 8).

---

## 5. Core EventBus & API Contracts

### EventBus Events
* **Commands:** `ride.start`, `ride.end`, `app.quit`
* **Recording:** `ride.started`, `ride.finished`, `state.changed`, `camera.lost`
* **Processing & Reel:** `processing.queued`, `processing.progress`, `moments.ready`, `reel.ready`, `processing.failed`
* **Sharing & System:** `share.created`, `tunnel.up`, `tunnel.down`, `app.notice`, `log.entry`

### Operator WebSocket (`/ws`)
Standard JSON envelope: `{ "type": "...", "id": "uuid", "ts": "ISO-8601", "payload": {} }`.
* **Client $\rightarrow$ Server:** `state.get`, `ride.start`, `ride.end`, `session.notes`, `logs.subscribe`, `logs.unsubscribe`, `ping`.
* **Server $\rightarrow$ Client:** `state`, `notice`, `preview.frame`, `camera.readiness`, `disk.status`, `log`, `processing.progress`, `reel.ready`, `share.created`, `tunnel.status`, `app.quit`, `error`, `pong`.

### Operator REST API (`http://127.0.0.1:8000/api`)
* `GET /health`, `GET /devices/cameras`
* `GET/PUT /config/cameras`, `GET/PUT /config/settings`
* `GET /sessions`, `GET/DELETE /sessions/{id}`, `POST /sessions/{id}/open-folder`, `POST /sessions/{id}/reprocess`, `GET /sessions/{id}/reel`
* `GET/POST/DELETE /sessions/{id}/share`
* `GET /tunnel/status`, `POST /tunnel/restart`

---

## 6. Phased Roadmap & Acceptance Gates
1. **Phase 1 — Recording Core:** 100 consecutive start/stop cycles produce playable MP4s; mid-ride camera unplug marks `partial`; crash recovery marks `interrupted`; sync calibrated within 1 frame (~40 ms).
2. **Phase 2 — Operator UI:** Full shift operable via the 5 UI pages (Console, Cameras, Settings, History, Logs) with auto-reconnecting WebSocket.
3. **Phase 3 — Computer Vision:** Validated against 20–30 hand-labeled rides; processes faster than ride cadence without dropping live recording frames.
4. **Phase 4 — Reel Builder:** Outputs within 1s of target length; plays and seeks cleanly on iPhone Safari and Android Chrome; failures preserve raw video for `Reprocess`.
5. **Phase 5 — Hardware Trigger:** Physical PCB button (serial/TCP/GPIO) survives 1,000 presses with no duplicates; unplugging falls back gracefully to keyboard/UI.
6. **Phase 6 — Public Sharing & Tunnel:** External mobile-data test confirms port `8000` routes return `404`; expired/revoked links return `404`; killed tunnel self-recovers.
7. **Phase 7 — QR Codes:** Scans reliably from 1 meter across multiple phone models.
8. **Phase 8 — Cloud Upload:** Optional S3/GCS upload behind `ReelPublisher`; upload failure never blocks local reel; video plays when venue PC is off.