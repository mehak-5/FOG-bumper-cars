import json

import pytest

from backend.app.core.atomic import atomic_write_json
from tests.conftest import VALID_ID

ORIGIN = {"origin": "http://127.0.0.1:8000"}


def make_session(ctx, session_id=VALID_ID, **meta):
    d = ctx.cfg.sessions_dir / f"ride_{session_id}"
    d.mkdir(parents=True)
    atomic_write_json(
        d / "session.json",
        {
            "session_id": session_id,
            "status": "completed",
            "start_time": "2026-01-01T12:00:00+00:00",
            "duration_seconds": 42.0,
            "notes": "",
            "cameras": [{"id": "camera_1", "status": "completed"}],
            **meta,
        },
    )
    return d


# ---------------- pages ----------------
@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ("/", "Start ride"),
        ("/cameras", "Save cameras"),
        ("/settings", "Save settings"),
        ("/history", "Ride history"),
        ("/logs", "Minimum level"),
    ],
)
def test_pages_render_with_nav(client, path, needle):
    r = client.get(path)
    assert r.status_code == 200 and needle in r.text
    assert 'href="/history"' in r.text and "Test Venue" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_console_lists_every_camera_with_preview_slot(client):
    html = client.get("/").text
    for cid in ("camera_1", "camera_2"):
        assert f'data-camera="{cid}"' in html and f'data-camera-card="{cid}"' in html
    assert "/static/js/console.js" in html


def test_vendored_assets_are_served_offline(client):
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert client.get("/static/vendor/pico.min.css").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    html = client.get("/").text
    assert "cdn" not in html and "http://" not in html and "https://" not in html


def test_static_does_not_expose_other_files(client):
    for bad in ("/static/../config.py", "/static/%2e%2e/config.py", "/static/js/../../main.py"):
        assert client.get(bad).status_code in (404, 400)


# ---------------- cameras ----------------
def form_for(rows, enabled=None):
    data = {
        "id": [r[0] for r in rows],
        "name": [r[1] for r in rows],
        "source": [r[2] for r in rows],
    }
    data["enabled"] = [str(i) for i in (enabled if enabled is not None else range(len(rows)))]
    return data


def test_cameras_save_roundtrip(client, ctx):
    rows = [("camera_1", "Front", "0"), ("camera_2", "Rear", "rtsp://h/s")]
    r = client.post("/cameras", data=form_for(rows, enabled=[0]), headers=ORIGIN)
    assert r.status_code == 200 and "Saved" in r.text
    cams = json.loads(json.dumps(client.get("/api/config/cameras").json()))
    assert cams[0] == {"id": "camera_1", "name": "Front", "source": 0}  # digits become an int
    assert cams[1]["enabled"] is False and cams[1]["source"] == "rtsp://h/s"


def test_cameras_invalid_not_saved(client, ctx):
    before = ctx.config_path.read_text()
    r = client.post("/cameras", data=form_for([("bad id", "X", "0")]), headers=ORIGIN)
    assert "Not saved" in r.text and 'data-kind="error"' in r.text
    assert ctx.config_path.read_text() == before


def test_add_and_remove_row_do_not_save(client, ctx):
    before = ctx.config_path.read_text()
    rows = [("camera_1", "Front", "0"), ("camera_2", "Rear", "1")]
    r = client.post("/cameras/add-row", data=form_for(rows), headers=ORIGIN)
    assert r.text.count('name="id"') == 3 and 'value="camera_3"' in r.text
    r = client.post("/cameras/remove-row?index=0", data=form_for(rows), headers=ORIGIN)
    assert r.text.count('name="id"') == 1 and 'value="camera_2"' in r.text
    assert ctx.config_path.read_text() == before


def test_user_text_is_escaped(client, ctx):
    evil = '<script>alert("x")</script>'
    client.post("/cameras", data=form_for([("camera_1", evil, "0")]), headers=ORIGIN)
    page = client.get("/cameras").text
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    make_session(ctx, notes=evil)
    rows = client.get("/history/rows").text
    assert "<script>alert" not in rows and "&lt;script&gt;" in rows


# ---------------- settings ----------------
def settings_form(**over):
    base = {
        "ffmpeg_path": "ffmpeg",
        "width": "1280",
        "height": "720",
        "fps": "25",
        "format": "mp4",
        "video_codec": "libx264",
        "encoder_args": "-preset veryfast -crf 23",
        "ride_duration_seconds": "",
        "max_duration_seconds": "600",
        "open_timeout_seconds": "10",
        "disconnect_timeout_seconds": "5",
        "debounce_seconds": "0.5",
    }
    return {**base, **over}


def test_settings_save(client):
    r = client.post(
        "/settings", data=settings_form(fps="30", ride_duration_seconds="90"), headers=ORIGIN
    )
    assert "Saved" in r.text
    saved = client.get("/api/config/settings").json()
    assert saved["fps"] == 30 and saved["ride_duration_seconds"] == 90
    assert saved["encoder_args"] == ["-preset", "veryfast", "-crf", "23"]
    assert saved["trigger"] == {"debounce_seconds": 0.5}


@pytest.mark.parametrize("over", [{"fps": "0"}, {"fps": "abc"}, {"width": "-5"}])
def test_settings_invalid_not_saved(client, ctx, over):
    before = ctx.config_path.read_text()
    r = client.post("/settings", data=settings_form(**over), headers=ORIGIN)
    assert "Not saved" in r.text
    assert ctx.config_path.read_text() == before


def test_settings_missing_field_is_handled(client):
    data = settings_form()
    del data["fps"]
    assert "Not saved" in client.post("/settings", data=data, headers=ORIGIN).text


# ---------------- history ----------------
def test_history_rows_empty_and_populated(client, ctx):
    assert "No rides recorded yet" in client.get("/history/rows").text
    make_session(ctx)
    html = client.get("/history/rows").text
    assert "2026-01-01 12:00:00" in html and "42s" in html and "camera_1: completed" in html


def test_history_delete_reprocess(client, ctx):
    d = make_session(ctx)
    r = client.post(f"/history/{VALID_ID}/reprocess", headers=ORIGIN)
    assert "Queued" in r.text
    assert json.loads((d / "session.json").read_text())["processing"] == {"status": "pending"}
    r = client.delete(f"/history/{VALID_ID}", headers=ORIGIN)
    assert "Ride deleted" in r.text and not d.exists()


def test_history_refuses_unknown_and_malicious_ids(client, ctx):
    d = make_session(ctx)
    for bad in ("..", "x", "20260101_120000_ZZZZZZ"):
        # ".." is normalised away by the HTTP client before it reaches us (405 on "/")
        assert client.delete(f"/history/{bad}", headers=ORIGIN).status_code in (404, 405)
        assert client.post(f"/history/{bad}/open", headers=ORIGIN).status_code in (404, 405)
    assert d.exists()


def test_history_cannot_delete_recording_ride(client, ctx):
    make_session(ctx, status="recording")
    r = client.post(f"/history/{VALID_ID}/reprocess", headers=ORIGIN)
    assert "cannot be reprocessed" in r.text


# ---------------- CSRF ----------------
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/cameras"),
        ("post", "/settings"),
        ("delete", f"/history/{VALID_ID}"),
        ("put", "/api/config/cameras"),
        ("delete", f"/api/sessions/{VALID_ID}"),
        ("post", f"/api/sessions/{VALID_ID}/reprocess"),
    ],
)
def test_foreign_origin_writes_are_refused(client, ctx, method, path):
    d = make_session(ctx)
    for origin in ("https://evil.example.com", "http://127.0.0.1:3000", "null"):
        r = getattr(client, method)(path, headers={"origin": origin})
        assert r.status_code == 403, origin
    assert d.exists()


def test_same_origin_and_originless_requests_pass(client, ctx):
    make_session(ctx)
    assert (
        client.get(
            f"/api/sessions/{VALID_ID}", headers={"origin": "https://evil.example.com"}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/sessions/{VALID_ID}", headers=ORIGIN).status_code == 200


def test_quit_button_is_hidden_until_the_desktop_shell_enables_it(client):
    html = client.get("/").text
    assert 'id="btn-quit"' in html and "hidden" in html.split('id="btn-quit"')[1].split(">")[0]
    assert "/static/js/desktop.js" in html
