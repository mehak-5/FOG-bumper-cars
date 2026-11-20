"""Tests for Step 19 packaging, PyInstaller spec, electron-builder config and CI workflow."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from backend.app.main import resolve_app_root
from backend.app.ui.router import resolve_ui_dir

ROOT = Path(__file__).resolve().parent.parent


def test_pyinstaller_spec_validity():
    spec_path = ROOT / "bumper-backend.spec"
    assert spec_path.exists(), "bumper-backend.spec must exist"
    content = spec_path.read_text(encoding="utf-8")
    assert "Analysis(" in content
    assert "EXE(" in content
    assert "backend/app/main.py" in content
    assert "backend/app/ui/templates" in content
    assert "backend/app/ui/static" in content
    assert 'name="bumper-backend"' in content

    # Check that the spec parses as valid Python code
    compile(content, str(spec_path), "exec")


def test_electron_builder_config():
    pkg_path = ROOT / "package.json"
    assert pkg_path.exists()
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))

    assert "build" in pkg
    build = pkg["build"]
    assert build.get("appId") == "com.fogtech.bumpercar"
    assert build.get("productName") == "Bumper Car Moments"
    assert build.get("directories", {}).get("output") == "release"

    extra_resources = build.get("extraResources", [])
    backend_res = [r for r in extra_resources if r.get("from") == "resources/backend"]
    assert len(backend_res) == 1
    assert backend_res[0].get("to") == "backend"

    scripts = pkg.get("scripts", {})
    assert "build:backend" in scripts
    assert "pack" in scripts
    assert "dist" in scripts


def test_ci_workflow_validity():
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml must exist"
    data = yaml.safe_load(ci_path.read_text(encoding="utf-8"))

    assert "jobs" in data
    assert "test" in data["jobs"]
    steps = data["jobs"]["test"]["steps"]
    step_runs = [s.get("run", "") for s in steps if "run" in s]
    combined_runs = "\n".join(step_runs)

    assert "ruff check ." in combined_runs
    assert "ruff format --check" in combined_runs
    assert "python -m pytest" in combined_runs
    assert "build_ts.py --check" in combined_runs
    assert "npm run typecheck" in combined_runs
    assert "npm test" in combined_runs


def test_build_backend_script_exists():
    script = ROOT / "tools" / "build_backend.py"
    assert script.exists()
    assert (ROOT / "tools" / "ci.py").exists()


def test_resolve_app_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BUMPER_APP_ROOT", str(tmp_path))
    assert resolve_app_root() == tmp_path.resolve()


def test_resolve_app_root_frozen(monkeypatch, tmp_path):
    monkeypatch.delenv("BUMPER_APP_ROOT", raising=False)
    fake_exe = tmp_path / "resources" / "backend" / "bumper-backend.exe"
    fake_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_exe.touch()

    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert resolve_app_root() == fake_exe.parent.resolve()


def test_resolve_ui_dir_frozen(monkeypatch, tmp_path):
    import sys

    fake_meipass = tmp_path / "meipass_temp"
    fake_ui = fake_meipass / "backend" / "app" / "ui"
    fake_ui.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

    assert resolve_ui_dir() == fake_ui
