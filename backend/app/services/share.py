"""Share service: manage public tokens in share_index.json and isolated media in storage/public/."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus, Events
from backend.app.services.session_store import read_session, session_dir

log = logging.getLogger("share.service")

DEFAULT_EXPIRY_HOURS = 72


def hash_token(token: str) -> str:
    """Compute hex SHA-256 hash of token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ShareService:
    def __init__(
        self,
        sessions_dir: Path,
        bus: EventBus,
        *,
        public_dir: Path | None = None,
        share_index_file: Path | None = None,
    ) -> None:
        self.sessions_dir = sessions_dir
        self.bus = bus
        self.public_dir = public_dir or (sessions_dir.parent / "public")
        self.public_dir.mkdir(parents=True, exist_ok=True)
        self.share_index_file = share_index_file or (sessions_dir.parent / "share_index.json")

    def _read_index(self) -> dict[str, dict[str, Any]]:
        if not self.share_index_file.is_file():
            return {}
        try:
            data = json.loads(self.share_index_file.read_text(encoding="utf-8"))
            return data.get("tokens", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_index(self, tokens: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(self.share_index_file, {"tokens": tokens})

    def create_share(
        self, session_id: str, expiry_hours: int = DEFAULT_EXPIRY_HOURS
    ) -> dict[str, Any]:
        """Generate token, copy/link media to storage/public/<session_id>, record hash in index."""
        sdir = session_dir(self.sessions_dir, session_id)
        meta = read_session(sdir)
        if meta is None:
            raise ValueError(f"Session {session_id} not found")

        reel_path = sdir / "reel.mp4"
        if not reel_path.is_file():
            raise ValueError(f"Reel video does not exist for session {session_id}")

        poster_path = sdir / "poster.jpg"

        # Copy or hard-link into storage/public/<session_id>
        pub_target_dir = self.public_dir / session_id
        pub_target_dir.mkdir(parents=True, exist_ok=True)

        pub_reel = pub_target_dir / "reel.mp4"
        if pub_reel.is_file():
            pub_reel.unlink(missing_ok=True)
        try:
            os.link(reel_path, pub_reel)
        except OSError:
            shutil.copy2(reel_path, pub_reel)

        if poster_path.is_file():
            pub_poster = pub_target_dir / "poster.jpg"
            if pub_poster.is_file():
                pub_poster.unlink(missing_ok=True)
            try:
                os.link(poster_path, pub_poster)
            except OSError:
                shutil.copy2(poster_path, pub_poster)

        # Generate cryptographically secure token
        token = secrets.token_urlsafe(24)
        token_hash = hash_token(token)
        now_dt = datetime.now(UTC)
        expires_at = (now_dt + timedelta(hours=expiry_hours)).isoformat()

        # Update share_index.json (stores only hash!)
        index = self._read_index()
        for _, entry in index.items():
            if entry.get("session_id") == session_id:
                entry["revoked"] = True

        index[token_hash] = {
            "session_id": session_id,
            "created_at": now_dt.isoformat(),
            "expires_at": expires_at,
            "revoked": False,
        }
        self._write_index(index)

        # Update session.json
        share_data = {
            "token": token,
            "token_hash": token_hash,
            "url_path": f"/r/{token}",
            "created_at": now_dt.isoformat(),
            "expires_at": expires_at,
            "revoked": False,
        }
        meta["share"] = share_data
        atomic_write_json(sdir / "session.json", meta)

        log.info("Created share token for session %s (expires %s)", session_id, expires_at)
        return share_data

    def get_share(self, session_id: str) -> dict[str, Any] | None:
        sdir = session_dir(self.sessions_dir, session_id)
        meta = read_session(sdir)
        if meta and isinstance(meta.get("share"), dict):
            return meta["share"]
        return None

    def revoke_share(self, session_id: str) -> bool:
        sdir = session_dir(self.sessions_dir, session_id)
        meta = read_session(sdir)
        if meta is None:
            return False

        index = self._read_index()
        revoked = False
        for _, entry in index.items():
            if entry.get("session_id") == session_id and not entry.get("revoked"):
                entry["revoked"] = True
                revoked = True
        if revoked:
            self._write_index(index)

        if isinstance(meta.get("share"), dict):
            meta["share"]["revoked"] = True
            atomic_write_json(sdir / "session.json", meta)
            revoked = True

        pub_target_dir = self.public_dir / session_id
        if pub_target_dir.is_dir():
            shutil.rmtree(pub_target_dir, ignore_errors=True)

        return revoked

    def resolve_token(self, token: str) -> dict[str, Any] | None:
        """Resolve token against index. Returns None if unknown, expired, or revoked."""
        token_hash = hash_token(token)
        index = self._read_index()
        entry = index.get(token_hash)
        if not entry or entry.get("revoked", False):
            return None

        exp_str = entry.get("expires_at")
        if exp_str:
            try:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt < datetime.now(UTC):
                    return None
            except Exception:
                return None

        sid = entry.get("session_id")
        if not sid:
            return None

        pub_dir = self.public_dir / sid
        reel = pub_dir / "reel.mp4"
        if not reel.is_file():
            return None

        poster = pub_dir / "poster.jpg"
        return {
            "session_id": sid,
            "reel_path": reel,
            "poster_path": poster if poster.is_file() else None,
            "entry": entry,
        }

    async def on_reel_ready(self, session_id: str) -> None:
        try:
            share_data = self.create_share(session_id)
            await self.bus.publish(
                Events.SHARE_CREATED,
                {
                    "session_id": session_id,
                    "url": share_data["url_path"],
                    "expires_at": share_data["expires_at"],
                },
            )
        except Exception:
            log.exception("Could not auto-create share for session %s", session_id)
