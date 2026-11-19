import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.core.config import AppConfig, load_config
from app.core.event_bus import EventBus
from app.core.state import AppState, TriggerState
from app.recorder.manager import RecorderManager
from app.recorder.preview import PreviewManager
from app.storage.session_store import SessionStore, check_disk_space, recover_interrupted_sessions
from app.triggers.keyboard import KeyboardTrigger

logger = logging.getLogger("fastapi_app")


def create_app(config_path: str = "config/venue.yaml") -> FastAPI:
    cfg = load_config(config_path)
    bus = EventBus()
    store = SessionStore(cfg.storage.sessions_dir)

    # 1. Startup Recovery & Disk checks
    recovered = recover_interrupted_sessions(Path(cfg.storage.sessions_dir))
    if recovered:
        logger.info("Recovered %d interrupted session(s) from previous crash", recovered)

    # 2. Preview & Recorder Managers
    ffmpeg_bin = cfg.recording.ffmpeg_path
    preview_manager = PreviewManager(cfg.cameras, cfg.recording, ffmpeg_bin)
    recorder = RecorderManager(cfg, bus, store, preview_manager, ffmpeg_bin)

    # 3. Triggers
    trigger = KeyboardTrigger(
        bus,
        debounce_seconds=cfg.trigger.debounce_seconds,
        on_trigger_state=lambda ts: recorder.update_trigger_state(ts),
    )

    app = FastAPI(title="Bumper Car Operator App", docs_url=None, redoc_url=None)

    # Setup Templates & Static
    templates_dir = Path("backend/app/ui/templates")
    static_dir = Path("backend/app/ui/static")
    templates = Jinja2Templates(directory=str(templates_dir))
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Connected WebSockets for UI
    active_websockets: list[WebSocket] = []

    # Forward all EventBus events to WebSocket clients via '*' wildcard
    async def forward_to_ws(name: str, payload: dict):
        if not active_websockets:
            return
        msg = json.dumps({"type": name, "payload": payload})
        for ws in list(active_websockets):
            try:
                await ws.send_text(msg)
            except Exception:
                pass

    bus.subscribe("*", forward_to_ws)

    @app.on_event("startup")
    async def on_startup():
        # Start keyboard trigger & preview pipes
        trigger.start()
        await preview_manager.start()

        # Start periodic preview frame pushing over WS (~2 fps)
        async def _preview_loop():
            while True:
                await asyncio.sleep(0.5)
                if active_websockets:
                    for c in cfg.cameras:
                        if c.enabled:
                            data_url = preview_manager.get_frame(c.id)
                            if data_url:
                                frame_msg = json.dumps({
                                    "type": "preview.frame",
                                    "payload": {"camera_id": c.id, "data_url": data_url},
                                })
                                for ws in list(active_websockets):
                                    try:
                                        await ws.send_text(frame_msg)
                                    except Exception:
                                        pass

        asyncio.create_task(_preview_loop())

        # Check disk space
        free_gb, is_warn = check_disk_space(Path(cfg.storage.sessions_dir), cfg.storage.disk_warn_gb)
        recorder.update_disk_status(free_gb)
        if is_warn:
            await bus.publish("app.notice", {"level": "warning", "text": f"Low disk space: {free_gb:.1f} GB free"})

    @app.on_event("shutdown")
    async def on_shutdown():
        trigger.stop()
        await preview_manager.stop()
        if recorder.ride.value == "recording":
            await recorder._finalize(reason="app_quit")

    # REST Endpoints
    @app.get("/api/health")
    async def get_health():
        return {"status": "ok", "venue": cfg.venue.name}

    @app.get("/api/state")
    async def get_state():
        return recorder.get_state().model_dump()

    @app.post("/api/trigger/key/{key}")
    async def post_trigger_key(key: str):
        keep_running = await trigger.handle_key(key.lower())
        return {"ok": True, "keep_running": keep_running}

    @app.get("/api/sessions")
    async def get_sessions():
        return store.list_sessions()

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        ok = store.delete_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"ok": True}

    # UI Pages (HTML via Jinja2)
    @app.get("/", response_class=HTMLResponse)
    async def get_index(request: Request):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "venue": cfg.venue.name,
                "cameras": cfg.cameras,
                "state": recorder.get_state(),
            },
        )

    # WebSocket for real-time events & commands
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        active_websockets.append(ws)

        # Immediately send full snapshot to newly connected client
        await ws.send_text(
            json.dumps({"type": "state.snapshot", "payload": recorder.get_state().model_dump()})
        )

        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type")
                    msg_id = msg.get("id")

                    if msg_type == "ride.start":
                        await bus.publish("ride.start", {"source": "ui"})
                        if msg_id:
                            await ws.send_text(json.dumps({"type": "ack", "id": msg_id, "ok": True}))
                    elif msg_type == "ride.end":
                        await bus.publish("ride.end", {"source": "ui", "reason": "ui_click"})
                        if msg_id:
                            await ws.send_text(json.dumps({"type": "ack", "id": msg_id, "ok": True}))
                except Exception as e:
                    logger.warning("Error processing WS message: %s", e)
        except WebSocketDisconnect:
            pass
        finally:
            if ws in active_websockets:
                active_websockets.remove(ws)

    return app


app = create_app()
