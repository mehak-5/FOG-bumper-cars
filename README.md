# Bumper Car Moments

An automated multi-camera recording, AI highlight extraction, reel generation, and guest sharing system for bumper-car attractions.

The system captures multi-camera video simultaneously during rides, extracts thrilling moments using computer vision and tracking, stitches a polished highlight reel with crossfades, and exposes a secure tokenized sharing portal accessible via QR codes over an encrypted tunnel.

---

## Architecture Overview

The system is designed with a **Python-first, recording-is-sacred** architecture:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Thin Electron Shell                             │
│       (BrowserWindow pointing to http://127.0.0.1:8000, desktop.quit) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / WebSocket
┌───────────────────────────────────▼────────────────────────────────────┐
│                  Private Operator App (Port 8000)                      │
│   FastAPI • Jinja2 + HTMX + Pico.css • WebSocket • Origin Guard        │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │                          EventBus                              │   │
│   └──────┬──────────────┬──────────────┬──────────────┬────────────┘   │
│          │              │              │              │                │
│   ┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐         │
│   │  Triggers   ││   Preview   ││  Recorder   ││  Startup &  │         │
│   │ (Keyb / UI) ││   Manager   ││   Manager   ││ Disk Guard  │         │
│   └─────────────┘└─────────────┘└──────┬──────┘└─────────────┘         │
└────────────────────────────────────────┼───────────────────────────────┘
                                         │ Writes MP4s
                                         ▼
                               ┌───────────────────┐
                               │  storage/sessions │
                               └─────────┬─────────┘
                                         │
                     ┌───────────────────┴───────────────────┐
                     │ Separate OS Process (Worker Queue)    │
                     │  • Extraction: YOLOv8 + ByteTrack     │
                     │  • Segmentation & Activity Ranking    │
                     │  • Reel Builder: FFmpeg xfade + audio │
                     └───────────────────┬───────────────────┘
                                         │ Output: reel.mp4 + poster.jpg
                                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    Public Share App (Port 8001)                        │
│   FastAPI (docs/redoc/openapi disabled)                                │
│   • Serves ONLY storage/public/<session_id>/reel.mp4 & poster.jpg      │
│   • SHA-256 URL-safe token authentication (72h expiry, revocable)      │
│   • HTTP Range 206 streaming for iOS Safari / modern mobile browsers   │
│   • Strict security headers (Cache-Control, X-Robots-Tag, No CORS)    │
└───────────────────────────────────▲────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴────────────────────────────────────┐
│                     Cloudflare / ngrok Tunnel                          │
│   Encrypted public tunnel mapped exclusively to port 8001              │
│   Auto-restart on failure, 30s health-check polling, secret masking    │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Python-First**: All business logic, video pipeline orchestration, machine learning inference, and API endpoints are written in Python (FastAPI). Electron is reduced to a thin TypeScript shell that boots the backend process and hosts the local operator web view.
2. **Recording is Sacred**: Camera recordings must never drop frames or stall. AI video processing, object detection, and reel generation run in an isolated background OS process spawned on demand, completely decoupled from active recording loops.
3. **Crash Safety**: Every metadata update (`session.json`, `venue.yaml`, `share_index.json`) is written via `atomic_write_json` / `atomic_write_text` using temporary files, OS-level `fsync`, and atomic file replacement (`os.replace`).
4. **Port & Network Isolation**: The operator interface binds strictly to `127.0.0.1:8000` with Host and Origin validation. The public share service binds to `127.0.0.1:8001`. Public app startup is refused if ports collide. The tunnel points exclusively to port 8001, guaranteeing that operator APIs and raw video recordings are unreachable externally.

---

## Directory Structure

```
FOG-bumper-cars/
├── backend/
│   ├── app/
│   │   ├── main.py                  # Operator app factory, lifespan & server runner
│   │   ├── config.py                # Pydantic venue.yaml schema & loader
│   │   ├── context.py               # Shared AppContext dependency container
│   │   ├── security.py              # OriginGuardMiddleware for port 8000
│   │   ├── api/
│   │   │   └── routes.py            # REST endpoints (sessions, config, tunnel, share)
│   │   ├── core/
│   │   │   ├── atomic.py            # Crash-safe atomic file writes (tmp -> fsync -> replace)
│   │   │   ├── events.py            # Async EventBus & event topic constants
│   │   │   ├── logger.py            # Structured logging & EventBus log streamer
│   │   │   └── models.py            # Pydantic models (SessionMeta, AppState, etc.)
│   │   ├── processing/
│   │   │   ├── extraction.py        # YOLO + ByteTrack object tracking
│   │   │   ├── segmentation.py      # 1s feature bins & activity peak detection
│   │   │   ├── ranking.py           # Multi-criteria scoring & greedy clip selection
│   │   │   ├── pipeline.py          # Staged execution (extract -> segment -> rank -> build)
│   │   │   ├── service.py           # Background job queue & worker manager
│   │   │   └── worker.py            # Dedicated worker process entry point
│   │   ├── public/
│   │   │   └── app.py               # Isolated public share app (port 8001, Range 206)
│   │   ├── recorder/
│   │   │   ├── camera.py            # CameraRecorder (FFmpeg subprocess manager)
│   │   │   ├── preview.py           # Persistent low-framerate JPEG preview pipe
│   │   │   └── recorder_manager.py  # Ride state machine (IDLE -> RECORDING -> IDLE)
│   │   ├── reel/
│   │   │   └── builder.py           # FFmpeg xfade normalization & assembly
│   │   ├── services/
│   │   │   ├── desktop.py           # OS desktop integration (reveal file in explorer)
│   │   │   ├── publisher.py         # ReelPublisher protocol (TunnelPublisher / Cloud)
│   │   │   ├── qr.py                # High-res QR code PNG and data URL generator
│   │   │   ├── session_store.py     # Session directory reading, listing & deletion
│   │   │   ├── share.py             # Token generation, SHA-256 index & public linking
│   │   │   ├── startup.py           # Startup crash recovery & disk space guard
│   │   │   └── tunnel.py            # cloudflared / ngrok supervisor & health poller
│   │   ├── triggers/
│   │   │   └── keyboard_trigger.py  # Global keyboard listener (pynput)
│   │   ├── ui/
│   │   │   ├── router.py            # Jinja2 SSR operator routes
│   │   │   ├── templates/           # Server-rendered HTML templates
│   │   │   ├── static/              # Pico.css, HTMX, and compiled JavaScript
│   │   │   └── ts/                  # TypeScript frontend modules (compiled by tsc)
│   │   └── ws/
│   │       ├── connection_manager.py# WebSocket broadcaster
│   │       └── router.py            # /ws endpoint
├── config/
│   ├── venue.yaml                   # Production hardware & venue configuration
│   └── venue.test.yaml              # Synthetic test configuration (video files)
├── electron/
│   ├── src/
│   │   ├── backend-process.ts       # Backend supervisor, health poller & graceful quit
│   │   ├── main.ts                  # Single-instance Electron window lifecycle
│   │   └── preload.ts               # Minimal contextBridge (desktop.quit)
│   └── test/
│       └── backend-process.test.mjs # Node.js unit tests for Electron runner
├── storage/
│   ├── sessions/                    # Raw ride recordings & session metadata
│   └── public/                      # Hard-linked/copied highlight reels for sharing
├── tests/                           # Complete pytest suite (220+ tests)
├── tools/
│   ├── build_backend.py             # PyInstaller packaging runner
│   ├── build_ts.py                  # TypeScript type-generation and build tool
│   ├── ci.py                        # Local CI check aggregator
│   ├── dev.py                       # Unified dev runner (backend + electron)
│   ├── list_cameras.py              # DirectShow webcam enumerator
│   └── make_test_videos.py          # Test fixture MP4 generator
├── bumper-backend.spec              # PyInstaller specification for bumper-backend.exe
├── package.json                     # Node / Electron dependencies & electron-builder config
└── pyproject.toml                   # Python dependencies & Ruff / Pytest configurations
```

---

## Configuration (`config/venue.yaml`)

The system configuration is validated at launch using Pydantic:

```yaml
venue:
  name: "Bumper Car Arena"

server:
  port: 8000                        # Operator interface port (127.0.0.1)
  public_port: 8001                 # Public guest share port (127.0.0.1)

recording:
  ffmpeg_path: "ffmpeg"             # System PATH or absolute executable path
  width: 1280
  height: 720
  fps: 25
  video_codec: "libx264"
  encoder_args: ["-preset", "veryfast", "-crf", "23"]
  ride_duration_seconds: null       # null = manual operator trigger, number = auto-stop
  max_duration_seconds: 600         # Hard safety cap
  open_timeout_seconds: 10
  disconnect_timeout_seconds: 5

trigger:
  start_key: "s"
  stop_key: "e"
  quit_key: "q"
  debounce_seconds: 0.5

storage:
  sessions_dir: "storage/sessions"
  public_dir: "storage/public"
  disk_warn_gb: 5

tunnel:
  enabled: false
  provider: "cloudflared"           # "cloudflared" | "ngrok"
  binary_path: "cloudflared"

reel:
  target_seconds: 45
  crossfade_seconds: 0.3
  both_cameras: false

cameras:
  - id: "camera_1"
    name: "North Arena"
    source: "0"                     # DirectShow index, device name, or RTSP URL
    enabled: true
    input_args: []

  - id: "camera_2"
    name: "South Arena"
    source: "1"
    enabled: true
    input_args: []
```

---

## Getting Started

### Prerequisites

- **Python 3.11+**
- **Node.js 20+**
- **FFmpeg & FFprobe**: available in your system `PATH` (e.g. `winget install Gyan.FFmpeg`)

### Installation

1. Clone repository and install Python dependencies:
   ```powershell
   git clone <repo-url>
   cd FOG-bumper-cars
   pip install -e .[dev]
   ```

2. Install Node dependencies and compile TypeScript UI code:
   ```powershell
   npm install
   python tools/build_ts.py
   ```

3. (Optional) Generate synthetic video fixtures for hardware-free development:
   ```powershell
   python tools/make_test_videos.py
   ```

---

## Running the Application

### Development Mode

Run the backend and Electron desktop shell together with auto-recompile:
```powershell
python tools/dev.py
```

Or run the Python operator backend directly:
```powershell
python -m backend.app.main
```
Then navigate to `http://127.0.0.1:8000` in any web browser.

### Hardware Testing Tools

List available DirectShow cameras on Windows:
```powershell
python tools/list_cameras.py
```

---

## Verification & Testing

The project maintains strict continuous verification with over 220 automated unit, integration, and security tests.

Run all pre-commit checks with one command:
```powershell
python tools/ci.py
```

Or run individual verification steps:

1. **Python Linting:**
   ```powershell
   ruff check .
   ```

2. **Code Formatting Check:**
   ```powershell
   ruff format --check backend tests tools
   ```

3. **Pytest Test Suite:**
   ```powershell
   python -m pytest
   ```

4. **TypeScript Model Synchronizer:**
   ```powershell
   python tools/build_ts.py --check
   ```

5. **TypeScript Compilation Check:**
   ```powershell
   npm run typecheck
   ```

6. **Electron Unit Tests:**
   ```powershell
   npm test
   ```

---

## Packaging & Production Deployment

### Building Standalone Executables

1. **Package Backend Executable**:
   Builds `bumper-backend.exe` with bundled Jinja2 templates, static assets, and hidden imports into `resources/backend/bumper-backend.exe`:
   ```powershell
   npm run build:backend
   ```

2. **Package Electron Windows Installer**:
   Generates a standalone NSIS installer under `release/`:
   ```powershell
   npm run dist
   ```

---

## Security Model

- **Local Operator Isolation**: The operator interface binds to `127.0.0.1:8000`. `OriginGuardMiddleware` validates `Host` and `Origin` headers on all incoming requests to prevent DNS rebinding or CSRF attacks from external sites.
- **Port Separation**: The guest sharing app runs on `127.0.0.1:8001` with API docs disabled (`docs_url=None`). It rejects startup if configured to the operator port.
- **Token Security**: Share links use cryptographically secure 24-byte URL-safe tokens (`secrets.token_urlsafe(24)`). Only the SHA-256 hash of the token is stored in `storage/share_index.json`. Unknown, expired, and revoked tokens return an indistinguishable generic 404 response.
- **Directory Traversal Protection**: Session IDs and tokens are validated against strict regex character whitelists before accessing disk paths.
- **Stream Sanitization**: RTSP camera credentials and tunnel tokens are masked in application logs and UI representations.
