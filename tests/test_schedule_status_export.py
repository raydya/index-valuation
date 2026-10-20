import importlib.util
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_schedule_status", ROOT / "scripts/export_schedule_status.py"
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_exporter_job_only_runs_metadata_export_and_keeps_business_schedules_separate(tmp_path):
    job = exporter.job_definition(tmp_path)
    assert job["StartInterval"] == 60 and job["RunAtLoad"] is True
    assert job["Label"] == "cn.index-valuation.schedule-status"
    assert job["ProgramArguments"] == [
        str(tmp_path / ".venv/bin/python"),
        str(tmp_path / "scripts/export_schedule_status.py"),
    ]
    assert "update-index" not in repr(job)
    assert "TELEGRAM" not in repr(job)


def test_installer_rejects_other_checkout_before_any_launchctl_call(tmp_path, monkeypatch):
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    target = target_dir / f"{exporter.LABEL}.plist"
    target.write_bytes(plistlib.dumps(exporter.job_definition(Path("/other/project"))))
    before = target.read_bytes()
    monkeypatch.setattr(
        exporter.subprocess, "run", lambda *a, **k: pytest.fail("must not inspect other task")
    )
    with pytest.raises(ValueError):
        exporter.install(tmp_path, tmp_path / "source", target_dir, "gui/501")
    assert target.read_bytes() == before


def test_uninstall_only_removes_own_status_job(tmp_path, monkeypatch):
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    target = target_dir / f"{exporter.LABEL}.plist"
    target.write_bytes(plistlib.dumps(exporter.job_definition(tmp_path)))
    business = target_dir / "cn.index-valuation.daily.us.plist"
    business.write_bytes(b"existing-business-job")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(exporter.subprocess, "run", run)
    exporter.uninstall(tmp_path, target_dir, "gui/501")
    assert not target.exists()
    assert business.read_bytes() == b"existing-business-job"
    assert calls == [
        ["/bin/launchctl", "print", "gui/501/cn.index-valuation.schedule-status"],
        ["/bin/launchctl", "bootout", "gui/501/cn.index-valuation.schedule-status"],
    ]


def test_generate_does_not_query_launchd_or_export_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter.sys, "platform", "darwin")
    monkeypatch.setattr(
        exporter.subprocess, "run", lambda *a, **k: pytest.fail("generate must be offline")
    )
    assert exporter.main(["--root", str(tmp_path), "--generate"]) == 0
    assert (tmp_path / f"data/scheduler/{exporter.LABEL}.plist").exists()
    assert not (tmp_path / "data/scheduler/host-status/status.json").exists()


def test_install_restores_previous_loaded_job_after_copy_failure(tmp_path, monkeypatch):
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    target = target_dir / f"{exporter.LABEL}.plist"
    previous = plistlib.dumps(exporter.job_definition(tmp_path))
    target.write_bytes(previous)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr=b"")

    def failed_copy(source, destination):
        destination.write_bytes(b"interrupted copy")
        raise OSError("simulated write failure")

    monkeypatch.setattr(exporter.subprocess, "run", run)
    monkeypatch.setattr(exporter.shutil, "copyfile", failed_copy)
    with pytest.raises(OSError):
        exporter.install(tmp_path, tmp_path / "new.plist", target_dir, "gui/501")
    assert target.read_bytes() == previous
    assert calls == [
        ["/bin/launchctl", "print", "gui/501/cn.index-valuation.schedule-status"],
        ["/bin/launchctl", "bootout", "gui/501/cn.index-valuation.schedule-status"],
        ["/bin/launchctl", "bootstrap", "gui/501", str(target)],
    ]
