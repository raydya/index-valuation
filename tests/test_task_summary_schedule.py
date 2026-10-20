import importlib.util
import plistlib
import subprocess
from datetime import datetime as RealDatetime
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "install_task_summary_schedule", ROOT / "scripts/install_task_summary_schedule.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture(autouse=True)
def china_clock(monkeypatch):
    class ChinaTime:
        @staticmethod
        def now():
            return SimpleNamespace(
                astimezone=lambda: RealDatetime(2026, 10, 20, tzinfo=timezone(timedelta(hours=8)))
            )

    monkeypatch.setattr(installer, "datetime", ChinaTime)


def prepare(root):
    for relative in (".venv/bin/python", "scripts/notify_task_summary.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    generated = root / "new.plist"
    generated.write_bytes(plistlib.dumps(installer.job_definition(root)))
    return generated


def test_job_runs_only_daily_summary_without_secrets_or_install_time_execution(tmp_path):
    job = installer.job_definition(tmp_path)
    assert job["Label"] == "cn.index-valuation.task-summary"
    assert job["ProgramArguments"] == [
        str(tmp_path / ".venv/bin/python"),
        str(tmp_path / "scripts/notify_task_summary.py"),
        "send",
    ]
    assert job["StartCalendarInterval"] == {"Hour": 18, "Minute": 0}
    assert job["RunAtLoad"] is False and job["KeepAlive"] is False
    assert job["StandardOutPath"].endswith("/logs/task-summary.stdout.log")
    assert job["StandardErrorPath"].endswith("/logs/task-summary.stderr.log")
    assert set(job["EnvironmentVariables"]) == {"PYTHONUNBUFFERED", "TZ"}


@pytest.mark.parametrize("arguments", [[], ["--generate"]])
def test_generation_does_not_query_launchd_or_send_messages(tmp_path, monkeypatch, arguments):
    prepare(tmp_path)
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *a, **k: pytest.fail("generate must stay offline")
    )
    assert installer.main(["--root", str(tmp_path), *arguments]) == 0
    generated = tmp_path / f"data/scheduler/{installer.LABEL}.plist"
    assert plistlib.loads(generated.read_bytes()) == installer.job_definition(tmp_path)
    assert not (tmp_path / "data/scheduler/notifications").exists()


def test_installer_refuses_wrong_system_timezone_before_writing(tmp_path, monkeypatch):
    class DifferentTimezone:
        @staticmethod
        def now():
            return SimpleNamespace(
                astimezone=lambda: RealDatetime(2026, 10, 20, tzinfo=timezone(timedelta(hours=-4)))
            )

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "datetime", DifferentTimezone)
    with pytest.raises(SystemExit) as error:
        installer.main(["--root", str(tmp_path)])
    assert error.value.code == 2
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("damage", ["other_project", "symlink", "different_command"])
def test_install_refuses_unrelated_task_without_querying_launchd(tmp_path, monkeypatch, damage):
    generated = prepare(tmp_path)
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    target = target_dir / f"{installer.LABEL}.plist"
    if damage == "symlink":
        target.symlink_to(generated)
    else:
        existing = installer.job_definition(
            Path("/other") if damage == "other_project" else tmp_path
        )
        if damage == "different_command":
            existing["ProgramArguments"].append("--other")
        target.write_bytes(plistlib.dumps(existing))
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *a, **k: pytest.fail("must not touch unrelated task")
    )
    with pytest.raises(ValueError):
        installer.install(tmp_path, generated, target_dir, "gui/501")


def test_first_install_preserves_five_business_jobs_and_only_bootstraps_summary(
    tmp_path, monkeypatch
):
    generated = prepare(tmp_path)
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    business = [
        target_dir / f"{label}.plist"
        for label in (
            "cn.index-valuation.daily.asia",
            "cn.index-valuation.daily.us",
            "cn.index-valuation.erp-sheet",
            "cn.portfolio.quotes.daily.asia",
            "cn.portfolio.quotes.daily.us",
        )
    ]
    for path in business:
        path.write_bytes(b"original-business-job")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=113 if command[1] == "print" else 0, stderr=b"")

    monkeypatch.setattr(installer.subprocess, "run", run)
    installer.install(tmp_path, generated, target_dir, "gui/501")
    target = target_dir / f"{installer.LABEL}.plist"
    assert target.read_bytes() == generated.read_bytes()
    assert all(path.read_bytes() == b"original-business-job" for path in business)
    assert calls == [
        ["/bin/launchctl", "print", f"gui/501/{installer.LABEL}"],
        ["/bin/launchctl", "bootstrap", "gui/501", str(target)],
    ]


@pytest.mark.parametrize("failure", ["write", "bootstrap"])
def test_failed_replacement_restores_previous_loaded_summary(tmp_path, monkeypatch, failure):
    generated = prepare(tmp_path)
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    target = target_dir / f"{installer.LABEL}.plist"
    previous = installer.job_definition(tmp_path)
    previous["StartCalendarInterval"]["Minute"] = 1
    original = plistlib.dumps(previous)
    target.write_bytes(original)
    calls = []
    attempts = 0
    real_write = installer._write

    def write(path, content):
        nonlocal attempts
        attempts += 1
        if failure == "write" and attempts == 1:
            raise OSError("simulated replacement failure")
        real_write(path, content)

    def run(command, **kwargs):
        calls.append(command)
        if failure == "bootstrap" and command[1] == "bootstrap":
            if sum(call[1] == "bootstrap" for call in calls) == 1:
                raise subprocess.CalledProcessError(5, command)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(installer, "_write", write)
    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises((OSError, subprocess.CalledProcessError)):
        installer.install(tmp_path, generated, target_dir, "gui/501")
    assert target.read_bytes() == original
    assert calls[-1] == ["/bin/launchctl", "bootstrap", "gui/501", str(target)]
    assert all(
        command[1] == "bootstrap" or command[-1] == f"gui/501/{installer.LABEL}"
        for command in calls
    )


def test_uninstall_removes_only_summary_job(tmp_path, monkeypatch):
    generated = prepare(tmp_path)
    target_dir = tmp_path / "agents"
    target_dir.mkdir()
    target = target_dir / f"{installer.LABEL}.plist"
    target.write_bytes(generated.read_bytes())
    other = target_dir / "cn.index-valuation.erp-sheet.plist"
    other.write_bytes(b"original-erp")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(installer.subprocess, "run", run)
    installer.uninstall(tmp_path, target_dir, "gui/501")
    assert not target.exists()
    assert other.read_bytes() == b"original-erp"
    assert calls == [
        ["/bin/launchctl", "print", f"gui/501/{installer.LABEL}"],
        ["/bin/launchctl", "bootout", f"gui/501/{installer.LABEL}"],
    ]
