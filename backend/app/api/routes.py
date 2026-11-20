"""Operator REST API (http://127.0.0.1:8000/api)."""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.responses import FileResponse

from backend.app.config import ConfigError, VenueFile, save_config_text
from backend.app.context import AppContext
from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import Events
from backend.app.recorder.devices import list_webcams
from backend.app.services import session_store
from backend.app.services.desktop import open_in_file_manager
from backend.app.services.qr import generate_qr_png
from backend.app.services.session_store import InvalidSessionId


def read_config_doc(ctx: AppContext) -> dict[str, Any]:
    try:
        return yaml.safe_load(ctx.config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as err:
        raise HTTPException(500, f"Cannot read config: {err}") from err


def save_config_doc(ctx: AppContext, doc: dict[str, Any]) -> dict[str, Any]:
    """Validate the whole document, then atomically replace venue.yaml."""
    try:
        VenueFile.model_validate(doc)
        save_config_text(ctx.config_path, yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    except (ConfigError, ValueError) as err:
        raise HTTPException(422, str(err)) from err
    return {"saved": True, "restart_required": True}  # recorders read config at startup


def _session_path(ctx: AppContext, session_id: str):
    try:
        return session_store.session_dir(ctx.cfg.sessions_dir, session_id)
    except InvalidSessionId as err:
        raise HTTPException(404, "Session not found") from err


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "ride": ctx.recorder.ride,
            "ffmpeg": ctx.fatal is None,
            "error": ctx.fatal,
        }

    @router.post("/app/quit")
    async def quit_app() -> dict[str, bool]:
        """Used by the desktop shell: stops any ride cleanly, then exits the backend."""
        await ctx.bus.publish(Events.APP_QUIT, {"source": "desktop"})
        return {"quitting": True}

    @router.get("/devices/cameras")
    async def cameras_available() -> dict[str, Any]:
        if sys.platform != "win32":
            return {"cameras": []}
        try:
            return {"cameras": await list_webcams(ctx.cfg.recording.ffmpeg_path)}
        except OSError as err:
            raise HTTPException(500, f"Cannot list cameras: {err}") from err

    # ---------------- config ----------------
    @router.get("/config/cameras")
    async def get_cameras() -> list[dict[str, Any]]:
        return read_config_doc(ctx).get("cameras") or []

    @router.put("/config/cameras")
    async def put_cameras(cameras: Annotated[list[dict[str, Any]], Body()]) -> dict[str, Any]:
        doc = read_config_doc(ctx)
        doc["cameras"] = cameras
        return save_config_doc(ctx, doc)

    @router.get("/config/settings")
    async def get_settings() -> dict[str, Any]:
        doc = read_config_doc(ctx)
        return {**(doc.get("recording") or {}), "trigger": doc.get("trigger") or {}}

    @router.put("/config/settings")
    async def put_settings(settings: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        doc = read_config_doc(ctx)
        trigger = settings.pop("trigger", None)
        doc["recording"] = settings
        if trigger is not None:
            doc["trigger"] = trigger
        return save_config_doc(ctx, doc)

    # ---------------- sessions ----------------
    @router.get("/sessions")
    async def sessions() -> list[dict[str, Any]]:
        return await asyncio.to_thread(session_store.list_sessions, ctx.cfg.sessions_dir)

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        meta = session_store.read_session(_session_path(ctx, session_id))
        if meta is None:
            raise HTTPException(404, "Session not found")
        return meta

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, bool]:
        _session_path(ctx, session_id)  # validates the id
        active = ctx.recorder.session
        if active is not None and active.id == session_id:
            raise HTTPException(409, "Cannot delete a ride that is still recording")
        if not await asyncio.to_thread(
            session_store.delete_session, ctx.cfg.sessions_dir, session_id
        ):
            raise HTTPException(404, "Session not found")
        return {"deleted": True}

    @router.post("/sessions/{session_id}/open-folder")
    async def open_folder(session_id: str) -> dict[str, bool]:
        path = _session_path(ctx, session_id)
        if not path.is_dir():
            raise HTTPException(404, "Session not found")
        await open_in_file_manager(path)
        return {"opened": True}

    @router.post("/sessions/{session_id}/reprocess")
    async def reprocess(session_id: str) -> dict[str, bool]:
        path = _session_path(ctx, session_id)
        meta = session_store.read_session(path)
        if meta is None:
            raise HTTPException(404, "Session not found")
        if meta.get("status") == "recording":
            raise HTTPException(409, "Ride is still recording")
        meta["processing"] = {"status": "pending"}
        atomic_write_json(path / "session.json", meta)
        await ctx.bus.publish(Events.PROCESSING_QUEUED, {"session_id": session_id})
        return {"queued": True}

    @router.get("/sessions/{session_id}/reel")
    async def get_reel(session_id: str) -> FileResponse:
        reel = _session_path(ctx, session_id) / "reel.mp4"
        if not reel.is_file():
            raise HTTPException(404, "No reel for this session")
        return FileResponse(reel, media_type="video/mp4")  # Starlette serves Range requests

    # ---------------- share ----------------
    @router.get("/sessions/{session_id}/share")
    async def get_session_share(session_id: str) -> dict[str, Any]:
        _session_path(ctx, session_id)
        if ctx.share is None:
            raise HTTPException(500, "Share service unavailable")
        share_data = ctx.share.get_share(session_id)
        if not share_data or share_data.get("revoked"):
            raise HTTPException(404, "Session not shared")
        return share_data

    @router.post("/sessions/{session_id}/share")
    async def create_session_share(session_id: str) -> dict[str, Any]:
        _session_path(ctx, session_id)
        if ctx.share is None:
            raise HTTPException(500, "Share service unavailable")
        try:
            return ctx.share.create_share(session_id)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @router.delete("/sessions/{session_id}/share")
    async def revoke_session_share(session_id: str) -> dict[str, bool]:
        _session_path(ctx, session_id)
        if ctx.share is None:
            raise HTTPException(500, "Share service unavailable")
        revoked = ctx.share.revoke_share(session_id)
        return {"revoked": revoked}

    @router.get("/sessions/{session_id}/qr")
    async def get_session_qr(session_id: str) -> Response:
        _session_path(ctx, session_id)
        if ctx.share is None:
            raise HTTPException(500, "Share service unavailable")
        share_data = ctx.share.get_share(session_id)
        if not share_data or share_data.get("revoked"):
            raise HTTPException(404, "Session not shared")

        tunnel_url = ctx.tunnel.public_url if ctx.tunnel and ctx.tunnel.public_url else ""
        if tunnel_url:
            full_url = f"{tunnel_url.rstrip('/')}{share_data['url_path']}"
        else:
            full_url = f"http://127.0.0.1:{ctx.cfg.server.public_port}{share_data['url_path']}"

        png_bytes = generate_qr_png(full_url)
        return Response(content=png_bytes, media_type="image/png")

    # ---------------- tunnel ----------------
    @router.get("/tunnel/status")
    async def get_tunnel_status() -> dict[str, Any]:
        if ctx.tunnel is None:
            return {"up": False, "url": None, "provider": "disabled"}
        return ctx.tunnel.status()

    @router.post("/tunnel/restart")
    async def restart_tunnel() -> dict[str, bool]:
        if ctx.tunnel is None:
            raise HTTPException(400, "Tunnel service is not running")
        await ctx.tunnel.restart()
        return {"restarting": True}

    return router
