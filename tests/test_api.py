import json
import time

import pytest

from backend.app.core.atomic import atomic_write_json
from tests.conftest import VALID_ID, WS


def make_session(ctx, session_id=VALID_ID, **meta):
    d = ctx.cfg.sessions_dir / f"ride_{session_id}"
    d.mkdir(parents=True)
    atomic_write_json(
        d / "session.json",
        {
            "session_id": session_id,
            "status": "completed",
            "start_time": "2026-01-01T12:00:00",
            **meta,
        },
    )
    return d


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "ride": "idle", "ffmpeg": True, "error": None}


def test_non_loopback_host_header_is_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400
    assert client.get("/api/health", headers={"host": "localhost:8000"}).status_code == 200


def test_docs_are_not_a_secret_surface_but_unknown_routes_404(client):
    assert client.get("/api/nope").status_code == 404


# ---------------- config ----------------
def test_get_config(client):
    cams = client.get("/api/config/cameras").json()
    assert [c["id"] for c in cams] == ["camera_1", "camera_2"]
    settings = client.get("/api/config/settings").json()
    assert settings["open_timeout_seconds"] == 0.5 and "trigger" in settings


def test_put_cameras_validates_and_saves_atomically(client, ctx):
    new = [{"id": "cam_x", "name": "X", "source": 0, "enabled": True}]
    r = client.put("/api/config/cameras", json=new)
    assert r.status_code == 200 and r.json() == {"saved": True, "restart_required": True}
    assert client.get("/api/config/cameras").json() == new
    assert not list(ctx.config_path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "bad",
    [
        [{"id": "bad id", "name": "X", "source": 0}],
        [{"id": "a", "name": "X"}],
        [{"id": "a", "name": "X", "source": 0, "bogus": 1}],
    ],
)
def test_put_invalid_cameras_rejected_and_file_unchanged(client, ctx, bad):
    before = ctx.config_path.read_text()
    assert client.put("/api/config/cameras", json=bad).status_code == 422
    assert ctx.config_path.read_text() == before


def test_put_settings(client):
    s = client.get("/api/config/settings").json()
    s["fps"] = 30
    s["trigger"] = {"debounce_seconds": 0.8}
    assert client.put("/api/config/settings", json=s).status_code == 200
    again = client.get("/api/config/settings").json()
    assert again["fps"] == 30 and again["trigger"] == {"debounce_seconds": 0.8}
    assert client.put("/api/config/settings", json={"fps": 0}).status_code == 422


# ---------------- sessions ----------------
def test_sessions_list_get_delete(client, ctx):
    assert client.get("/api/sessions").json() == []
    make_session(ctx)
    make_session(ctx, "20260102_120000_abcdef", start_time="2026-01-02T12:00:00")
    ids = [s["session_id"] for s in client.get("/api/sessions").json()]
    assert ids == ["20260102_120000_abcdef", VALID_ID]  # newest first
    assert client.get(f"/api/sessions/{VALID_ID}").json()["status"] == "completed"
    assert client.delete(f"/api/sessions/{VALID_ID}").json() == {"deleted": True}
    assert client.get(f"/api/sessions/{VALID_ID}").status_code == 404
    assert client.delete(f"/api/sessions/{VALID_ID}").status_code == 404


@pytest.mark.parametrize(
    "bad_id", ["..", "..%2f..%2fetc", "ride_x", "20260101_120000_ABCDEF", "x" * 300, "%2e%2e"]
)
def test_malicious_session_ids_never_touch_disk(client, ctx, bad_id):
    make_session(ctx)
    for call in (client.get, client.delete, client.post):
        url = f"/api/sessions/{bad_id}" + ("/open-folder" if call == client.post else "")
        assert call(url).status_code in (404, 405)
    assert client.get(f"/api/sessions/{VALID_ID}").status_code == 200  # still there


def test_corrupt_session_is_skipped(client, ctx):
    d = ctx.cfg.sessions_dir / f"ride_{VALID_ID}"
    d.mkdir(parents=True)
    (d / "session.json").write_text("{broken")
    assert client.get("/api/sessions").json() == []
    assert client.get(f"/api/sessions/{VALID_ID}").status_code == 404


def test_reprocess_marks_pending_and_publishes(client, ctx):
    make_session(ctx, processing={"status": "failed", "stage": "ranking", "stderr": "boom"})
    r = client.post(f"/api/sessions/{VALID_ID}/reprocess")
    assert r.json() == {"queued": True}
    assert client.get(f"/api/sessions/{VALID_ID}").json()["processing"] == {"status": "pending"}
    assert client.post("/api/sessions/20260101_120000_000000/reprocess").status_code == 404


def test_reprocess_refused_while_recording(client, ctx):
    make_session(ctx, status="recording")
    assert client.post(f"/api/sessions/{VALID_ID}/reprocess").status_code == 409


def test_reel_endpoint(client, ctx):
    d = make_session(ctx)
    assert client.get(f"/api/sessions/{VALID_ID}/reel").status_code == 404
    (d / "reel.mp4").write_bytes(b"0123456789")
    r = client.get(f"/api/sessions/{VALID_ID}/reel")
    assert r.status_code == 200 and r.content == b"0123456789"
    assert r.headers["content-type"] == "video/mp4"


def test_cannot_delete_active_ride(client, ctx):
    with client.websocket_connect(WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "ride.start"})
        for _ in range(100):
            if ctx.recorder.ride == "recording":
                break
            time.sleep(0.05)
        sid = ctx.recorder.session.id
        assert client.delete(f"/api/sessions/{sid}").status_code == 409
        ws.send_json({"type": "ride.end"})
        for _ in range(100):
            if ctx.recorder.ride == "idle":
                break
            time.sleep(0.05)
    assert json.loads(next(ctx.cfg.sessions_dir.iterdir()).joinpath("session.json").read_text())


def test_open_folder_unknown_session_404(client):
    assert client.post("/api/sessions/20260101_120000_000000/open-folder").status_code == 404
