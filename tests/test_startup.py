import json

from backend.app.core.events import EventBus, Events
from backend.app.services import startup
from backend.app.services.startup import (
    StartupService,
    check_disk,
    clean_tmp_files,
    recover_interrupted_sessions,
    reset_stuck_jobs,
)


def make_session(root, name, **meta):
    d = root / name
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({"session_id": name, **meta}))
    return d


def read(d):
    return json.loads((d / "session.json").read_text())


def test_recording_sessions_become_interrupted(tmp_path):
    a = make_session(tmp_path, "ride_a", status="recording", end_time="x")
    b = make_session(tmp_path, "ride_b", status="completed")
    assert recover_interrupted_sessions(tmp_path) == 1
    assert read(a)["status"] == "interrupted"
    assert read(a)["end_time"] is None and read(a)["stop_reason"] == "interrupted"
    assert read(b)["status"] == "completed"


def test_recovery_is_idempotent(tmp_path):
    make_session(tmp_path, "ride_a", status="recording")
    assert recover_interrupted_sessions(tmp_path) == 1
    assert recover_interrupted_sessions(tmp_path) == 0


def test_recovery_tolerates_missing_dir_corrupt_and_foreign_entries(tmp_path):
    assert recover_interrupted_sessions(tmp_path / "nope") == 0
    d = tmp_path / "ride_bad"
    d.mkdir()
    (d / "session.json").write_text("{not json")
    (tmp_path / "ride_empty").mkdir()
    (tmp_path / "notes.txt").write_text("hi")
    make_session(tmp_path, "other_folder", status="recording")  # not a ride_ folder
    (tmp_path / "ride_list").mkdir()
    (tmp_path / "ride_list" / "session.json").write_text("[1]")
    assert recover_interrupted_sessions(tmp_path) == 0
    assert (d / "session.json").read_text() == "{not json"  # untouched


def test_stuck_processing_jobs_reset_to_pending(tmp_path):
    a = make_session(tmp_path, "ride_a", status="completed", processing={"status": "ranking"})
    b = make_session(tmp_path, "ride_b", status="completed", processing={"status": "done"})
    c = make_session(tmp_path, "ride_c", status="completed", processing={"status": "failed"})
    d = make_session(tmp_path, "ride_d", status="completed")
    assert reset_stuck_jobs(tmp_path) == 1
    assert read(a)["processing"]["status"] == "pending"
    assert read(b)["processing"]["status"] == "done"
    assert read(c)["processing"]["status"] == "failed"  # failed jobs keep their error for retry
    assert "processing" not in read(d)


def test_tmp_files_cleaned_recursively(tmp_path):
    d = make_session(tmp_path, "ride_a", status="completed")
    (d / "session.json.tmp").write_text("half")
    (tmp_path / "x.tmp").write_text("half")
    (d / "keep.mp4").write_bytes(b"v")
    assert clean_tmp_files(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [
        "keep.mp4",
        "session.json",
    ]
    assert clean_tmp_files(tmp_path / "missing") == 0


def test_half_written_tmp_never_replaces_real_session(tmp_path):
    d = make_session(tmp_path, "ride_a", status="recording")
    (d / "session.json.tmp").write_text('{"status": "comple')
    import asyncio

    asyncio.run(StartupService(tmp_path, 0).run())
    assert read(d)["status"] == "interrupted"


def test_check_disk_uses_nearest_existing_parent(tmp_path):
    s = check_disk(tmp_path / "a" / "b" / "c", warn_gb=0)
    assert s.free_gb > 0 and s.warn is False
    assert check_disk(tmp_path, warn_gb=10**9).warn is True


def test_check_disk_failure_does_not_crash(tmp_path, monkeypatch):
    def boom(_):
        raise OSError("nope")

    monkeypatch.setattr(startup.shutil, "disk_usage", boom)
    s = check_disk(tmp_path, 5)
    assert s.warn is False


async def test_service_publishes_notices(tmp_path):
    make_session(tmp_path, "ride_a", status="recording")
    bus = EventBus()
    notices = []
    bus.subscribe(Events.APP_NOTICE, notices.append)
    report = await StartupService(tmp_path, warn_gb=10**9, bus=bus).run()
    assert report.interrupted == 1 and report.disk.warn
    texts = " ".join(n["text"] for n in notices)
    assert "interrupted" in texts and "Low disk space" in texts
    assert all(n["level"] == "warning" for n in notices)


async def test_service_quiet_when_healthy(tmp_path):
    bus = EventBus()
    notices = []
    bus.subscribe(Events.APP_NOTICE, notices.append)
    report = await StartupService(tmp_path, warn_gb=0, bus=bus).run()
    assert (report.interrupted, report.jobs_reset, report.tmp_removed) == (0, 0, 0)
    assert notices == []
