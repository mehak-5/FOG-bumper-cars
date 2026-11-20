"""ReelPublisher protocol and implementations (TunnelPublisher and CloudPublisher stub)."""

from __future__ import annotations

from typing import Any, Protocol


class ReelPublisher(Protocol):
    """Protocol for publishing highlight reels to public destinations."""

    async def publish_reel(self, session_id: str, share_data: dict[str, Any]) -> str:
        """Return the complete public URL for accessing the highlight reel."""
        ...


class TunnelPublisher:
    """Publishes highlight reels through the local HTTP tunnel (Cloudflare / ngrok)."""

    def __init__(self, tunnel_url: str | None = None) -> None:
        self.tunnel_url = (tunnel_url or "").rstrip("/")

    def set_tunnel_url(self, url: str | None) -> None:
        self.tunnel_url = (url or "").rstrip("/")

    async def publish_reel(self, session_id: str, share_data: dict[str, Any]) -> str:
        url_path = share_data.get("url_path") or f"/r/{share_data.get('token', '')}"
        base = self.tunnel_url
        if not base:
            return url_path
        return f"{base}{url_path}"


class CloudPublisher:
    """Stub implementation for Phase 8: Cloud storage upload (S3 / GCS presigned URLs)."""

    def __init__(self, bucket: str = "bumper-reels", region: str = "us-east-1") -> None:
        self.bucket = bucket
        self.region = region

    async def publish_reel(self, session_id: str, share_data: dict[str, Any]) -> str:
        raise NotImplementedError("Cloud upload is available in Phase 8")
