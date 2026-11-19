import json
import os

import pytest

from backend.app.core import atomic
from backend.app.core.atomic import atomic_write_bytes, atomic_write_json, atomic_write_text


def test_writes_json_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "session.json"
    atomic_write_json(p, {"status": "completed", "名前": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"status": "completed", "名前": 1}
    assert list(tmp_path.iterdir()) == [p]


def test_overwrites_existing_file(tmp_path):
    p = tmp_path / "a.txt"
    atomic_write_text(p, "old")
    atomic_write_text(p, "new")
    assert p.read_text() == "new"


def test_fsync_happens_before_replace(tmp_path, monkeypatch):
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(
        os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1]
    )
    atomic_write_text(tmp_path / "x", "data")
    assert calls.index("fsync") < calls.index("replace")


def test_crash_before_fsync_keeps_old_file(tmp_path, monkeypatch):
    p = tmp_path / "session.json"
    atomic_write_json(p, {"v": "old"})

    def boom(fd):
        raise OSError("power loss")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_json(p, {"v": "new"})
    assert json.loads(p.read_text()) == {"v": "old"}
    assert not (tmp_path / "session.json.tmp").exists()


def test_crash_during_replace_keeps_old_file(tmp_path, monkeypatch):
    p = tmp_path / "session.json"
    atomic_write_json(p, {"v": "old"})

    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(p, {"v": "new"})
    assert json.loads(p.read_text()) == {"v": "old"}
    assert not (tmp_path / "session.json.tmp").exists()


def test_failed_first_write_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        atomic_write_bytes(tmp_path / "new.json", b"{}")
    assert list(tmp_path.iterdir()) == []


def test_unserializable_object_touches_nothing(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, {"ok": 1})
    with pytest.raises(TypeError):
        atomic_write_json(p, {"bad": object()})
    assert json.loads(p.read_text()) == {"ok": 1}
    assert list(tmp_path.iterdir()) == [p]


def test_directory_fsync_failure_after_replace_still_has_new_file(tmp_path, monkeypatch):
    # The rename already happened; surfacing the error must not roll back or corrupt the file.
    p = tmp_path / "s.json"
    monkeypatch.setattr(atomic, "_fsync_dir", lambda d: (_ for _ in ()).throw(OSError("dir")))
    with pytest.raises(OSError):
        atomic_write_text(p, "new")
    assert p.read_text() == "new"
