"""Tests for Step 18: Tunnel manager, ReelPublisher protocols, QR code generation, and APIs."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus, Events
from backend.app.services.publisher import CloudPublisher, TunnelPublisher
from backend.app.services.qr import generate_qr_data_url, generate_qr_png
from backend.app.services.tunnel import TunnelManager, mask_secrets


def test_mask_secrets():
    cmd = "cloudflared tunnel run --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-ID"
    masked = mask_secrets(cmd)
    assert "eyJ" not in masked
    assert "[REDACTED" in masked

    ngrok_cmd = "ngrok http 8001 --authtoken secret_auth_12345"
    masked_ngrok = mask_secrets(ngrok_cmd)
    assert "secret_auth_12345" not in masked_ngrok
    assert "--authtoken [REDACTED]" in masked_ngrok


def test_qr_code_generation():
    url = "https://fun-arena.trycloudflare.com/r/abc123token"
    png_bytes = generate_qr_png(url)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png_bytes) > 100

    data_url = generate_qr_data_url(url)
    assert data_url.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_reel_publisher_implementations():
    tunnel_pub = TunnelPublisher(tunnel_url="https://demo.trycloudflare.com")
    url = await tunnel_pub.publish_reel("session_1", {"url_path": "/r/token123"})
    assert url == "https://demo.trycloudflare.com/r/token123"

    tunnel_pub.set_tunnel_url("https://new.trycloudflare.com/")
    url2 = await tunnel_pub.publish_reel("session_1", {"url_path": "/r/token123"})
    assert url2 == "https://new.trycloudflare.com/r/token123"

    cloud_pub = CloudPublisher()
    with pytest.raises(NotImplementedError, match="Phase 8"):
        await cloud_pub.publish_reel("session_1", {})


def test_tunnel_manager_command_building():
    bus = EventBus()
    # Cloudflared quick tunnel
    tm_cf = TunnelManager(bus, public_port=8001, provider="cloudflared")
    assert tm_cf.build_command() == ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8001"]

    # Cloudflared named tunnel
    tm_cf_named = TunnelManager(
        bus, public_port=8001, provider="cloudflared", token="jwt_token_here"
    )
    assert tm_cf_named.build_command() == [
        "cloudflared",
        "tunnel",
        "run",
        "--token",
        "jwt_token_here",
    ]

    # ngrok
    tm_ng = TunnelManager(bus, public_port=8001, provider="ngrok", token="ngrok_tok")
    assert tm_ng.build_command() == ["ngrok", "http", "8001", "--authtoken", "ngrok_tok"]


class FakeStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        await asyncio.sleep(0.01)
        return b""


class FakeProcess:
    def __init__(self, lines: list[bytes]):
        self.stdout = FakeStream(lines)
        self.stderr = FakeStream([])
        self._exit_code = 0

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:
        pass


@pytest.mark.asyncio
async def test_tunnel_manager_lifecycle_and_url_detection():
    bus = EventBus()
    up_events = []
    down_events = []
    bus.subscribe(Events.TUNNEL_UP, lambda d: up_events.append(d))
    bus.subscribe(Events.TUNNEL_DOWN, lambda d: down_events.append(d))

    fake_lines = [
        b"2026-01-01 INF Starting tunnel...",
        b"2026-01-01 INF +---------------------------------------------------------+",
        b"2026-01-01 INF |  https://my-test-tunnel.trycloudflare.com               |",
        b"2026-01-01 INF +---------------------------------------------------------+",
    ]

    async def fake_spawn(cmd):
        return FakeProcess(fake_lines)

    async def fake_health(url):
        return "my-test-tunnel" in url

    tm = TunnelManager(
        bus=bus,
        public_port=8001,
        poll_interval=0.2,
        spawn_fn=fake_spawn,
        health_check_fn=fake_health,
    )

    await tm.start()
    try:
        # Wait up to 2s for tunnel to come up
        for _ in range(20):
            if tm.is_up and up_events:
                break
            await asyncio.sleep(0.1)

        assert tm.is_up is True
        assert tm.public_url == "https://my-test-tunnel.trycloudflare.com"
        assert len(up_events) >= 1
        assert up_events[0]["url"] == "https://my-test-tunnel.trycloudflare.com"

        # Check status
        st = tm.status()
        assert st["up"] is True
        assert st["url"] == "https://my-test-tunnel.trycloudflare.com"
    finally:
        await tm.stop()

    assert tm.is_up is False
    assert len(down_events) >= 1


def test_tunnel_and_qr_api_endpoints(client, ctx):
    # Set public_url on ctx.tunnel
    if ctx.tunnel:
        ctx.tunnel.public_url = "https://tunnel-demo.trycloudflare.com"
        ctx.tunnel.is_up = True

    # 1. GET /api/tunnel/status
    res = client.get("/api/tunnel/status")
    assert res.status_code == 200
    data = res.json()
    assert "up" in data

    # 2. POST /api/tunnel/restart
    res_restart = client.post("/api/tunnel/restart")
    assert res_restart.status_code == 200
    assert res_restart.json() == {"restarting": True}

    # 3. GET /api/sessions/{session_id}/qr
    sid = "20260101_130000_123456"
    sdir = ctx.cfg.sessions_dir / f"ride_{sid}"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "reel.mp4").write_bytes(b"REEL_VIDEO")
    atomic_write_json(
        sdir / "session.json",
        {
            "session_id": sid,
            "status": "completed",
            "start_time": "2026-01-01T13:00:00",
            "cameras": [],
        },
    )

    # Initially not shared -> 404
    assert client.get(f"/api/sessions/{sid}/qr").status_code == 404

    # Create share
    assert ctx.share is not None
    ctx.share.create_share(sid)

    # Now QR code returns 200 PNG
    qr_res = client.get(f"/api/sessions/{sid}/qr")
    assert qr_res.status_code == 200
    assert qr_res.headers["content-type"] == "image/png"
    assert qr_res.content.startswith(b"\x89PNG\r\n\x1a\n")
