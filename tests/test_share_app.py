"""Tests for Step 17: Public Share App (port 8001), ShareService, tokens and security isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.config import (
    AppConfig,
    CameraConfig,
    FileSource,
    RecordingConfig,
    ServerConfig,
    TriggerConfig,
    VenueInfo,
)
from backend.app.core.atomic import atomic_write_json
from backend.app.core.events import EventBus
from backend.app.public.app import create_public_app
from backend.app.services.share import ShareService, hash_token

SESSION_ID = "20260101_120000_123456"


@pytest.fixture
def test_setup(tmp_path: Path):
    sessions_dir = tmp_path / "storage" / "sessions"
    public_dir = tmp_path / "storage" / "public"
    share_index_file = tmp_path / "storage" / "share_index.json"
    sessions_dir.mkdir(parents=True)
    public_dir.mkdir(parents=True)

    bus = EventBus()
    share_service = ShareService(
        sessions_dir=sessions_dir,
        bus=bus,
        public_dir=public_dir,
        share_index_file=share_index_file,
    )

    cfg = AppConfig(
        venue=VenueInfo(name="Test Venue"),
        recording=RecordingConfig(),
        trigger=TriggerConfig(),
        server=ServerConfig(port=8000, public_port=8001),
        sessions_dir=sessions_dir,
        cameras=[CameraConfig(id="cam1", name="Cam 1", source=FileSource(path="cam.mp4"))],
        disk_warn_gb=1.0,
    )

    # Create dummy ride session with raw video and built reel
    sdir = sessions_dir / f"ride_{SESSION_ID}"
    sdir.mkdir(parents=True)
    dummy_reel_data = b"DUMMY_REEL_VIDEO_CONTENT_BYTES_FOR_STREAMING_RANGE_TEST" * 20
    dummy_poster_data = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"DUMMY_JPEG_DATA" * 10
    raw_cam_data = b"RAW_CAMERA_UNSHARED_VIDEO"

    (sdir / "reel.mp4").write_bytes(dummy_reel_data)
    (sdir / "poster.jpg").write_bytes(dummy_poster_data)
    (sdir / "cam1.mp4").write_bytes(raw_cam_data)

    session_meta = {
        "session_id": SESSION_ID,
        "status": "completed",
        "duration_seconds": 30.0,
        "cameras": [{"id": "cam1", "file": "cam1.mp4"}],
        "reel": {"path": "reel.mp4", "poster_path": "poster.jpg"},
    }
    atomic_write_json(sdir / "session.json", session_meta)

    return {
        "cfg": cfg,
        "sessions_dir": sessions_dir,
        "public_dir": public_dir,
        "share_index_file": share_index_file,
        "share_service": share_service,
        "sdir": sdir,
        "reel_data": dummy_reel_data,
        "poster_data": dummy_poster_data,
    }


def test_refuse_start_if_ports_match(tmp_path: Path):
    sessions_dir = tmp_path / "storage" / "sessions"
    sessions_dir.mkdir(parents=True)
    server_cfg = ServerConfig.model_construct(port=8000, public_port=8000)
    cfg = AppConfig.model_construct(
        venue=VenueInfo(name="Test"),
        recording=RecordingConfig(),
        trigger=TriggerConfig(),
        server=server_cfg,
        sessions_dir=sessions_dir,
        cameras=[],
        disk_warn_gb=0,
    )
    bus = EventBus()
    share_service = ShareService(sessions_dir, bus)
    with pytest.raises(ValueError, match="must differ from operator port"):
        create_public_app(cfg, share_service)


@pytest.mark.asyncio
async def test_share_token_creation_and_index_hash_only(test_setup):
    service: ShareService = test_setup["share_service"]
    share_data = service.create_share(SESSION_ID, expiry_hours=48)

    assert "token" in share_data
    assert "token_hash" in share_data
    assert share_data["token_hash"] == hash_token(share_data["token"])

    # Check that storage/share_index.json does NOT contain the plain token
    index_file: Path = test_setup["share_index_file"]
    raw_index_text = index_file.read_text(encoding="utf-8")
    assert share_data["token"] not in raw_index_text
    assert share_data["token_hash"] in raw_index_text

    # Media copied/linked to public dir
    pub_dir: Path = test_setup["public_dir"] / SESSION_ID
    assert (pub_dir / "reel.mp4").is_file()
    assert (pub_dir / "poster.jpg").is_file()
    # Raw camera or session.json must NEVER be in public dir
    assert not (pub_dir / "session.json").exists()
    assert not (pub_dir / "cam1.mp4").exists()


@pytest.mark.asyncio
async def test_public_routes_happy_path(test_setup):
    cfg = test_setup["cfg"]
    service: ShareService = test_setup["share_service"]
    share_data = service.create_share(SESSION_ID)
    token = share_data["token"]

    app = create_public_app(cfg, service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        # 1. Healthz
        res = await client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        assert res.headers["Cache-Control"] == "private, no-store"
        assert res.headers["X-Robots-Tag"] == "noindex, nofollow"

        # 2. Player HTML
        res = await client.get(f"/r/{token}")
        assert res.status_code == 200
        assert "<video" in res.text
        assert f"/r/{token}/video" in res.text
        assert f"/r/{token}/poster" in res.text

        # 3. Poster
        res = await client.get(f"/r/{token}/poster")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")
        assert res.content == test_setup["poster_data"]

        # 4. Download
        res = await client.get(f"/r/{token}/download")
        assert res.status_code == 200
        assert res.headers["content-type"] == "video/mp4"
        assert "attachment" in res.headers.get("content-disposition", "")
        assert res.content == test_setup["reel_data"]


@pytest.mark.asyncio
async def test_http_range_206_streaming_for_ios(test_setup):
    cfg = test_setup["cfg"]
    service: ShareService = test_setup["share_service"]
    share_data = service.create_share(SESSION_ID)
    token = share_data["token"]

    app = create_public_app(cfg, service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        # Full content
        full_res = await client.get(f"/r/{token}/video")
        assert full_res.status_code == 200
        total_len = len(test_setup["reel_data"])
        assert len(full_res.content) == total_len

        # Partial range request (mandatory for iOS Safari seeking)
        headers = {"Range": "bytes=0-49"}
        range_res = await client.get(f"/r/{token}/video", headers=headers)
        assert range_res.status_code == 206
        assert range_res.headers["Content-Range"] == f"bytes 0-49/{total_len}"
        assert len(range_res.content) == 50
        assert range_res.content == test_setup["reel_data"][:50]

        # Offset range request
        headers2 = {"Range": "bytes=50-99"}
        range_res2 = await client.get(f"/r/{token}/video", headers=headers2)
        assert range_res2.status_code == 206
        assert range_res2.headers["Content-Range"] == f"bytes 50-99/{total_len}"
        assert range_res2.content == test_setup["reel_data"][50:100]


@pytest.mark.asyncio
async def test_security_unknown_expired_revoked_identical_404(test_setup):
    cfg = test_setup["cfg"]
    service: ShareService = test_setup["share_service"]

    # 1. Unknown token
    app = create_public_app(cfg, service)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        res_unknown = await client.get("/r/totally_unknown_token_123")
        assert res_unknown.status_code == 404
        assert res_unknown.text == "Not Found"

        # 2. Expired token
        share_expired = service.create_share(SESSION_ID, expiry_hours=-1)
        res_expired = await client.get(f"/r/{share_expired['token']}")
        assert res_expired.status_code == 404
        assert res_expired.text == res_unknown.text

        # 3. Revoked token
        share_valid = service.create_share(SESSION_ID, expiry_hours=72)
        service.revoke_share(SESSION_ID)
        res_revoked = await client.get(f"/r/{share_valid['token']}")
        assert res_revoked.status_code == 404
        assert res_revoked.text == res_unknown.text

        # 4. Missing sub-resources return identical 404
        assert (await client.get("/r/totally_unknown_token_123/video")).status_code == 404
        assert (await client.get("/r/totally_unknown_token_123/poster")).status_code == 404
        assert (await client.get("/r/totally_unknown_token_123/download")).status_code == 404


@pytest.mark.asyncio
async def test_strict_isolation_operator_routes_unreachable_on_share_app(test_setup):
    cfg = test_setup["cfg"]
    service: ShareService = test_setup["share_service"]
    app = create_public_app(cfg, service)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        # None of the operator routes may exist on 8001
        assert (await client.get("/api/health")).status_code == 404
        assert (await client.get("/api/sessions")).status_code == 404
        assert (await client.get("/api/config/settings")).status_code == 404
        assert (await client.post("/api/app/quit")).status_code == 404
        assert (await client.get("/")).status_code == 404
        # Cannot serve raw camera video or session.json
        assert (
            await client.get(f"/storage/sessions/ride_{SESSION_ID}/cam1.mp4")
        ).status_code == 404
        assert (
            await client.get(f"/storage/sessions/ride_{SESSION_ID}/session.json")
        ).status_code == 404


@pytest.mark.asyncio
async def test_per_ip_rate_limiting(test_setup):
    cfg = test_setup["cfg"]
    service: ShareService = test_setup["share_service"]
    # Limit to 5 requests per window for test
    app = create_public_app(cfg, service, rate_limit_max=5, rate_limit_window=60.0)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8001") as client:
        ip_a = {"CF-Connecting-IP": "203.0.113.195"}
        for _ in range(5):
            res = await client.get("/healthz", headers=ip_a)
            assert res.status_code == 200

        # 6th request from IP A must be rate-limited
        res_limit = await client.get("/healthz", headers=ip_a)
        assert res_limit.status_code == 429
        assert "Too Many Requests" in res_limit.text

        # Different IP B is NOT blocked
        ip_b = {"CF-Connecting-IP": "203.0.113.196"}
        res_b = await client.get("/healthz", headers=ip_b)
        assert res_b.status_code == 200


def test_operator_share_endpoints(client, ctx):
    # Test GET/POST/DELETE /api/sessions/{session_id}/share on operator app
    sid = "20260101_120000_123456"
    sdir = ctx.cfg.sessions_dir / f"ride_{sid}"
    sdir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        sdir / "session.json",
        {
            "session_id": sid,
            "status": "completed",
            "start_time": "2026-01-01T12:00:00",
            "cameras": [],
        },
    )

    # Put reel.mp4 and poster.jpg so share can be created
    (sdir / "reel.mp4").write_bytes(b"REEL_VIDEO_CONTENT")
    (sdir / "poster.jpg").write_bytes(b"POSTER_CONTENT")

    # 1. Initially not shared -> 404
    res = client.get(f"/api/sessions/{sid}/share")
    assert res.status_code == 404

    # 2. Create share -> 200
    res_post = client.post(f"/api/sessions/{sid}/share")
    assert res_post.status_code == 200
    share_doc = res_post.json()
    assert "token" in share_doc
    assert share_doc["url_path"].startswith("/r/")

    # 3. GET share -> 200
    res_get = client.get(f"/api/sessions/{sid}/share")
    assert res_get.status_code == 200
    assert res_get.json()["token"] == share_doc["token"]

    # 4. Revoke share -> 200
    res_del = client.delete(f"/api/sessions/{sid}/share")
    assert res_del.status_code == 200
    assert res_del.json() == {"revoked": True}

    # 5. GET share after revoke -> 404
    res_after = client.get(f"/api/sessions/{sid}/share")
    assert res_after.status_code == 404
