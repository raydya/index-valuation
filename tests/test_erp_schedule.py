import importlib.util
import json
import plistlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
THREAD = "01a13ebd-dc5d-7192-8d98-933910a4362d"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_script("install_erp_schedule")
runner = load_script("run_erp_sync")


def prepare(root):
    (root / "docs").mkdir()
    # Arguments must remain literal text, including newlines and shell metacharacters.
    (root / "docs/erp-sheet-update-task.md").write_text("Google Drive only\n`literal` $(literal)")


def test_daily_8am_job_uses_existing_thread_and_separate_logs(tmp_path):
    job = installer.job_definition(tmp_path, Path("/opt/homebrew/bin/codex"), THREAD)
    assert job["StartCalendarInterval"] == {"Hour": 8, "Minute": 0}
    assert job["ProgramArguments"][-2:] == ["--thread", THREAD]
    assert "run_erp_sync.py" in job["ProgramArguments"][1]
    assert job["StandardOutPath"].endswith("/logs/erp-sheet.stdout.log")
    assert not job.get("RunAtLoad") and not job.get("KeepAlive")


def test_queue_is_shell_free_deduplicates_only_receipts_and_probe_does_not_block_update(
    tmp_path, monkeypatch
):
    prepare(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="accepted\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", run)
    codex = Path("/path with spaces/codex")
    state = tmp_path / "data/scheduler/erp-sheet/last-queued.json"
    assert runner.queue_update(tmp_path, codex, THREAD, probe=True) == 0
    assert not state.exists()
    assert runner.queue_update(tmp_path, codex, THREAD) == 0
    assert runner.queue_update(tmp_path, codex, THREAD) == 0
    assert len(calls) == 2
    command, kwargs = calls[1]
    assert command[:5] == [str(codex), "queue", "--thread", THREAD, "--message"]
    assert command[5].endswith("Google Drive only\n`literal` $(literal)")
    assert not kwargs.get("shell") and kwargs["timeout"] == 60
    assert json.loads(state.read_text())["status"] == "queued"


@pytest.mark.parametrize("failure", [3, "timeout"])
def test_failed_or_ambiguous_submission_is_not_marked_queued(tmp_path, monkeypatch, failure):
    prepare(tmp_path)

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=failure, stdout="", stderr="unavailable")

    monkeypatch.setattr(runner.subprocess, "run", run)
    expected = 124 if failure == "timeout" else failure
    assert runner.queue_update(tmp_path, Path("/bin/codex"), THREAD) == expected
    assert not (tmp_path / "data/scheduler/erp-sheet/last-queued.json").exists()


def test_installer_does_not_replace_an_unrelated_job(tmp_path, monkeypatch):
    generated = tmp_path / f"{installer.LABEL}.plist"
    generated.write_bytes(
        plistlib.dumps(installer.job_definition(tmp_path, Path("/codex"), THREAD))
    )
    targets = tmp_path / "LaunchAgents"
    targets.mkdir()
    target = targets / generated.name
    original = plistlib.dumps({"WorkingDirectory": "/other", "ProgramArguments": ["unrelated"]})
    target.write_bytes(original)

    def unexpected(*_args, **_kwargs):
        pytest.fail("No launchctl command may run before ownership is checked")

    monkeypatch.setattr(installer.subprocess, "run", unexpected)
    with pytest.raises(ValueError, match="未覆盖"):
        installer.install_job(tmp_path, generated, targets, "gui/501")
    assert target.read_bytes() == original


def test_failed_reinstall_restores_previous_job(tmp_path, monkeypatch):
    targets = tmp_path / "LaunchAgents"
    targets.mkdir()
    generated = tmp_path / f"{installer.LABEL}.plist"
    payload = installer.job_definition(tmp_path, Path("/codex"), THREAD)
    generated.write_bytes(plistlib.dumps(payload))
    payload["StartCalendarInterval"] = {"Hour": 7, "Minute": 0}
    original = plistlib.dumps(payload)
    target = targets / generated.name
    target.write_bytes(original)
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[1] == "bootstrap" and target.read_bytes() != original:
            raise subprocess.CalledProcessError(5, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install_job(tmp_path, generated, targets, "gui/501")
    assert target.read_bytes() == original
    assert [c[1] for c in calls] == ["print", "bootout", "enable", "bootstrap", "bootstrap"]
