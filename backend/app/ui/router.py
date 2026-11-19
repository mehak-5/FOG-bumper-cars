"""Server-rendered operator pages (Jinja2 + HTMX). All state-changing actions are plain forms."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.app.api.routes import read_config_doc, save_config_doc
from backend.app.context import AppContext
from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import Events
from backend.app.services import session_store
from backend.app.services.desktop import open_in_file_manager
from backend.app.services.session_store import InvalidSessionId

UI_DIR = Path(__file__).resolve().parent
NAV = [
    ("console", "Console", "/"),
    ("cameras", "Cameras", "/cameras"),
    ("settings", "Settings", "/settings"),
    ("history", "History", "/history"),
    ("logs", "Logs", "/logs"),
]


def _num(raw: str, kind: type[int] | type[float]) -> int | float:
    return kind(raw.strip())


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    templates = Jinja2Templates(directory=str(UI_DIR / "templates"))  # autoescape is on

    def page(request: Request, name: str, active: str, title: str, **extra: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            {
                "nav": NAV,
                "active": active,
                "page_title": title,
                "venue": ctx.cfg.venue.name or "Venue",
                **extra,
            },
        )

    # ---------------- console / logs ----------------
    @router.get("/", response_class=HTMLResponse)
    async def console(request: Request) -> HTMLResponse:
        return page(request, "console.html", "console", "Console", state=ctx.recorder.get_state())

    @router.get("/logs", response_class=HTMLResponse)
    async def logs(request: Request) -> HTMLResponse:
        return page(request, "logs.html", "logs", "Logs")

    # ---------------- cameras ----------------
    def camera_rows() -> list[dict[str, Any]]:
        return [
            {**c, "enabled": c.get("enabled", True), "source": str(c.get("source", ""))}
            for c in read_config_doc(ctx).get("cameras") or []
        ]

    def form_rows(form: Any) -> list[dict[str, Any]]:
        ids, names, sources = form.getlist("id"), form.getlist("name"), form.getlist("source")
        enabled = set(form.getlist("enabled"))
        return [
            {"id": i, "name": n, "source": s, "enabled": str(idx) in enabled}
            for idx, (i, n, s) in enumerate(zip(ids, names, sources, strict=False))
        ]

    def cameras_partial(
        request: Request, rows: list[dict[str, Any]], message: str = "", kind: str = "ok"
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "cameras_form.html",
            {"rows": rows, "message": message, "message_kind": kind},
        )

    @router.get("/cameras", response_class=HTMLResponse)
    async def cameras(request: Request) -> HTMLResponse:
        return page(
            request,
            "cameras.html",
            "cameras",
            "Cameras",
            rows=camera_rows(),
            message="",
            message_kind="ok",
        )

    @router.post("/cameras/add-row", response_class=HTMLResponse)
    async def add_row(request: Request) -> HTMLResponse:
        rows = form_rows(await request.form())
        rows.append({"id": f"camera_{len(rows) + 1}", "name": "", "source": "", "enabled": True})
        return cameras_partial(request, rows)

    @router.post("/cameras/remove-row", response_class=HTMLResponse)
    async def remove_row(request: Request, index: int = -1) -> HTMLResponse:
        rows = form_rows(await request.form())
        if 0 <= index < len(rows):
            del rows[index]
        return cameras_partial(request, rows)

    @router.post("/cameras", response_class=HTMLResponse)
    async def save_cameras(request: Request) -> HTMLResponse:
        rows = form_rows(await request.form())
        doc = read_config_doc(ctx)
        extras = {c.get("id"): c for c in doc.get("cameras") or []}
        cams = []
        for r in rows:
            source: int | str = (
                int(r["source"]) if r["source"].strip().isdigit() else r["source"].strip()
            )
            cam = {"id": r["id"].strip(), "name": r["name"].strip(), "source": source}
            if not r["enabled"]:
                cam["enabled"] = False
            if "input_args" in extras.get(cam["id"], {}):
                cam["input_args"] = extras[cam["id"]][
                    "input_args"
                ]  # keep what the form cannot edit
            cams.append(cam)
        doc["cameras"] = cams
        try:
            save_config_doc(ctx, doc)
        except HTTPException as err:
            return cameras_partial(request, rows, f"Not saved: {err.detail}", "error")
        return cameras_partial(request, rows, "Saved. Restart the app to apply.", "ok")

    # ---------------- settings ----------------
    def settings_values(doc: dict[str, Any]) -> dict[str, Any]:
        rec = doc.get("recording") or {}
        base = ctx.cfg.recording.model_dump()
        merged = {**base, **rec}
        return {
            **merged,
            "encoder_args": " ".join(merged.get("encoder_args") or []),
            "ride_duration_seconds": merged.get("ride_duration_seconds") or "",
            "max_duration_seconds": merged.get("max_duration_seconds") or "",
            "debounce_seconds": (doc.get("trigger") or {}).get(
                "debounce_seconds", ctx.cfg.trigger.debounce_seconds
            ),
        }

    def settings_partial(request: Request, values: dict[str, Any], msg: str = "", kind: str = "ok"):
        return templates.TemplateResponse(
            request, "settings_form.html", {"s": values, "message": msg, "message_kind": kind}
        )

    @router.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request) -> HTMLResponse:
        return page(
            request,
            "settings.html",
            "settings",
            "Settings",
            s=settings_values(read_config_doc(ctx)),
            message="",
            message_kind="ok",
        )

    @router.post("/settings", response_class=HTMLResponse)
    async def save_settings(request: Request) -> HTMLResponse:
        f = dict((await request.form()).items())
        doc = read_config_doc(ctx)
        try:
            rec = {
                "ffmpeg_path": f["ffmpeg_path"].strip(),
                "width": _num(f["width"], int),
                "height": _num(f["height"], int),
                "fps": _num(f["fps"], int),
                "format": f["format"].strip(),
                "video_codec": f["video_codec"].strip(),
                "encoder_args": shlex.split(f.get("encoder_args", "")),
                "ride_duration_seconds": _num(f["ride_duration_seconds"], float)
                if f.get("ride_duration_seconds", "").strip()
                else None,
                "max_duration_seconds": _num(f["max_duration_seconds"], float)
                if f.get("max_duration_seconds", "").strip()
                else None,
                "open_timeout_seconds": _num(f["open_timeout_seconds"], float),
                "disconnect_timeout_seconds": _num(f["disconnect_timeout_seconds"], float),
            }
            doc["recording"] = rec
            doc["trigger"] = {"debounce_seconds": _num(f["debounce_seconds"], float)}
            save_config_doc(ctx, doc)
        except (KeyError, ValueError) as err:
            return settings_partial(
                request, settings_values({"recording": f}), f"Not saved: {err}", "error"
            )
        except HTTPException as err:
            return settings_partial(
                request, settings_values(doc), f"Not saved: {err.detail}", "error"
            )
        return settings_partial(request, settings_values(doc), "Saved. Restart the app to apply.")

    # ---------------- history ----------------
    async def history_partial(request: Request, msg: str = "", kind: str = "ok") -> HTMLResponse:
        sessions = await asyncio.to_thread(session_store.list_sessions, ctx.cfg.sessions_dir)
        return templates.TemplateResponse(
            request,
            "history_rows.html",
            {"sessions": sessions, "message": msg, "message_kind": kind},
        )

    def session_path(session_id: str) -> Path:
        try:
            return session_store.session_dir(ctx.cfg.sessions_dir, session_id)
        except InvalidSessionId as err:
            raise HTTPException(404, "Session not found") from err

    @router.get("/history", response_class=HTMLResponse)
    async def history(request: Request) -> HTMLResponse:
        return page(request, "history.html", "history", "History")

    @router.get("/history/rows", response_class=HTMLResponse)
    async def history_rows(request: Request) -> HTMLResponse:
        return await history_partial(request)

    @router.delete("/history/{session_id}", response_class=HTMLResponse)
    async def history_delete(request: Request, session_id: str) -> HTMLResponse:
        session_path(session_id)
        active = ctx.recorder.session
        if active is not None and active.id == session_id:
            return await history_partial(
                request, "Cannot delete a ride that is still recording.", "error"
            )
        await asyncio.to_thread(session_store.delete_session, ctx.cfg.sessions_dir, session_id)
        return await history_partial(request, "Ride deleted.")

    @router.post("/history/{session_id}/open", response_class=HTMLResponse)
    async def history_open(request: Request, session_id: str) -> HTMLResponse:
        path = session_path(session_id)
        if not path.is_dir():
            return await history_partial(request, "Ride folder not found.", "error")
        await open_in_file_manager(path)
        return await history_partial(request)

    @router.post("/history/{session_id}/reprocess", response_class=HTMLResponse)
    async def history_reprocess(request: Request, session_id: str) -> HTMLResponse:
        path = session_path(session_id)
        meta = session_store.read_session(path)
        if meta is None or meta.get("status") == "recording":
            return await history_partial(request, "This ride cannot be reprocessed.", "error")
        meta["processing"] = {"status": "pending"}
        atomic_write_json(path / "session.json", meta)
        await ctx.bus.publish(Events.PROCESSING_QUEUED, {"session_id": session_id})
        return await history_partial(request, "Queued for processing.")

    return router
