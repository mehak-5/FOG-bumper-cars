"""App factory, lifespan and EventBus wiring for the private operator app (127.0.0.1:8000)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.api.routes import build_router as build_api_router
from backend.app.config import load_config
from backend.app.context import AppContext
from backend.app.core.events import EventBus, Events
from backend.app.core.logger import BusLogHandler, setup_file_logging
from backend.app.processing.service import ProcessingService
from backend.app.recorder.camera import SpawnFn
from backend.app.recorder.preview import PreviewManager
from backend.app.recorder.recorder_manager import RecorderManager
from backend.app.security import OriginGuardMiddleware
from backend.app.services.publisher import TunnelPublisher
from backend.app.services.share import ShareService
from backend.app.services.startup import StartupService, check_disk
from backend.app.services.tunnel import TunnelManager
from backend.app.triggers.keyboard_trigger import KeyboardTrigger
from backend.app.ui.router import UI_DIR
from backend.app.ui.router import build_router as build_ui_router
from backend.app.ws.connection_manager import ConnectionManager
from backend.app.ws.router import build_router as build_ws_router

log = logging.getLogger("backend")
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]


def resolve_app_root() -> Path:
    if "BUMPER_APP_ROOT" in os.environ:
        return Path(os.environ["BUMPER_APP_ROOT"]).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


APP_ROOT = resolve_app_root()


def probe_ffmpeg(ffmpeg_path: str) -> str | None:
    """None if ffmpeg runs, otherwise a human-readable problem."""
    try:
        result = subprocess.run([ffmpeg_path, "-version"], capture_output=True, timeout=10)
        if result.returncode == 0:
            return None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return (
        f"ffmpeg not found at '{ffmpeg_path}'. "
        "Install it or set recording.ffmpeg_path / FFMPEG_PATH."
    )


def create_app(
    *,
    config_path: str | Path | None = None,
    app_root: Path = APP_ROOT,
    data_root: Path | None = None,
    spawn_fn: SpawnFn | None = None,
    preview_spawn_fn: SpawnFn | None = None,
    processing_spawn_fn: Any | None = None,
    check_ffmpeg: bool = True,
    start_preview: bool = True,
    start_processing: bool = True,
    start_tunnel: bool = False,
    tunnel_spawn_fn: Any | None = None,
    keyboard: bool = False,
    file_logging: bool = False,
    on_quit: Callable[[], None] | None = None,
    camera_opts: dict[str, Any] | None = None,
) -> FastAPI:
    data_root = data_root or app_root
    config_path = Path(config_path or os.environ.get("BUMPER_CONFIG") or "config/venue.yaml")
    if not config_path.is_absolute():
        config_path = app_root / config_path
    cfg = load_config(config_path, app_root, data_root)  # raises a readable ConfigError

    bus = EventBus()
    ws = ConnectionManager()
    tasks: set[asyncio.Task[Any]] = set()

    def on_frame(camera_id: str, frame: bytes) -> None:
        if ws.count:
            import base64

            data_url = "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii")
            task = asyncio.get_running_loop().create_task(
                ws.broadcast("preview.frame", {"camera_id": camera_id, "data_url": data_url})
            )
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    preview = PreviewManager(
        cfg.cameras, cfg.recording, spawn_fn=preview_spawn_fn, on_frame=on_frame
    )
    recorder = RecorderManager(
        cfg, bus, spawn_fn=spawn_fn, preview=preview, camera_opts=camera_opts
    )
    trigger = KeyboardTrigger(
        bus, cfg.trigger.debounce_seconds, on_trigger_state=recorder.update_trigger_state
    )
    processing = ProcessingService(cfg.sessions_dir, bus, spawn_fn=processing_spawn_fn)
    share = ShareService(cfg.sessions_dir, bus)
    tunnel = TunnelManager(
        bus,
        public_port=cfg.server.public_port,
        spawn_fn=tunnel_spawn_fn,
    )
    publisher = TunnelPublisher(tunnel.public_url)
    ctx = AppContext(
        cfg=cfg,
        config_path=config_path,
        app_root=app_root,
        data_root=data_root,
        bus=bus,
        recorder=recorder,
        preview=preview,
        trigger=trigger,
        ws=ws,
        processing=processing,
        share=share,
        tunnel=tunnel,
        publisher=publisher,
    )

    # ---- EventBus -> WebSocket bridge ----
    bus.subscribe(Events.STATE_CHANGED, lambda s: ws.broadcast("state", s))
    bus.subscribe(Events.APP_NOTICE, lambda n: ws.broadcast("notice", n))
    bus.subscribe(Events.LOG_ENTRY, lambda e: ws.broadcast("log", e, logs_only=True))
    bus.subscribe(
        Events.PROCESSING_PROGRESS,
        lambda p: ws.broadcast("processing.progress", p),
    )
    bus.subscribe(Events.REEL_READY, lambda r: ws.broadcast("reel.ready", r))
    bus.subscribe(Events.REEL_READY, lambda r: share.on_reel_ready(r["session_id"]))
    bus.subscribe(Events.SHARE_CREATED, lambda s: ws.broadcast("share.created", s))

    async def _on_tunnel_up(d: dict[str, Any]) -> None:
        publisher.set_tunnel_url(d.get("url"))
        await ws.broadcast("tunnel.status", {"up": True, "url": d.get("url")})

    async def _on_tunnel_down(_d: dict[str, Any]) -> None:
        publisher.set_tunnel_url(None)
        await ws.broadcast("tunnel.status", {"up": False, "url": None})

    bus.subscribe(Events.TUNNEL_UP, _on_tunnel_up)
    bus.subscribe(Events.TUNNEL_DOWN, _on_tunnel_down)
    bus.subscribe(Events.APP_QUIT, lambda _d: _quit(ws, on_quit))

    async def readiness_loop() -> None:
        last: dict[str, Any] | None = None
        while True:
            await asyncio.sleep(0.5)
            current = preview.get_readiness()
            if current != last:
                last = current
                await ws.broadcast("camera.readiness", {"readiness": current})

    async def disk_loop() -> None:
        while True:
            status = check_disk(cfg.sessions_dir, cfg.disk_warn_gb)
            recorder.update_disk_status(status.free_gb)
            await ws.broadcast("disk.status", {"free_gb": status.free_gb, "warn": status.warn})
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        file_handler = setup_file_logging(data_root / "logs") if file_logging else None
        ctx.log_handler = BusLogHandler(bus, loop)
        logging.getLogger().addHandler(ctx.log_handler)

        if check_ffmpeg:
            ctx.fatal = await asyncio.to_thread(probe_ffmpeg, cfg.recording.ffmpeg_path)
            if ctx.fatal:
                log.error(ctx.fatal)
                recorder.set_fatal(ctx.fatal)
        await StartupService(cfg.sessions_dir, cfg.disk_warn_gb, bus).run()

        background = [asyncio.create_task(readiness_loop()), asyncio.create_task(disk_loop())]
        if start_preview and not ctx.fatal:
            preview.start()
        if start_processing:
            processing.start()
        if start_tunnel:
            await tunnel.start()
        if keyboard:
            trigger.start(loop)
        log.info(
            "Venue: %s | %d camera(s) | config: %s", cfg.venue.name, len(cfg.cameras), config_path
        )
        try:
            yield
        finally:
            trigger.stop()
            for t in background:
                t.cancel()
            preview.stop()
            if start_tunnel:
                await tunnel.stop()
            if start_processing:
                await processing.stop()
            await recorder.shutdown()  # stop any active ride cleanly before exiting
            for t in [*background, *tasks]:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            logging.getLogger().removeHandler(ctx.log_handler)
            if file_handler:
                logging.getLogger().removeHandler(file_handler)
                file_handler.close()

    app = FastAPI(title="Bumper Car Operator", lifespan=lifespan)
    # Defence against DNS-rebinding: only answer requests addressed to a loopback host name.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    # Defence against CSRF: a web page on another origin cannot trigger writes.
    app.add_middleware(OriginGuardMiddleware, port=cfg.server.port)
    app.state.ctx = ctx
    app.include_router(build_api_router(ctx))
    app.include_router(build_ws_router(ctx))
    app.include_router(build_ui_router(ctx))
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")
    return app


async def _quit(ws: ConnectionManager, on_quit: Callable[[], None] | None) -> None:
    await ws.broadcast("app.quit", {})
    if on_quit:
        on_quit()


def run() -> None:
    """Entry point: serve the operator app on 127.0.0.1 only."""
    import multiprocessing

    multiprocessing.freeze_support()

    import uvicorn

    holder: dict[str, uvicorn.Server] = {}

    def stop_server() -> None:
        if "server" in holder:
            holder["server"].should_exit = True

    app = create_app(keyboard=True, file_logging=True, on_quit=stop_server)
    cfg = app.state.ctx.cfg
    holder["server"] = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=cfg.server.port, log_level="info")
    )
    holder["server"].run()


if __name__ == "__main__":
    run()
