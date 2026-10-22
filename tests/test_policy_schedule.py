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
    "install_policy_schedule", ROOT / "scripts/install_policy_schedule.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
DOMAIN = "gui/501"


@pytest.fixture(autouse=True)
def china_clock(monkeypatch):
    class ChinaTime:
        @staticmethod
        def now():
            return SimpleNamespace(
                astimezone=lambda: RealDatetime(2026, 10, 22, tzinfo=timezone(timedelta(hours=8)))
            )

    monkeypatch.setattr(installer, "datetime", ChinaTime)
    monkeypatch.setattr(installer.sys, "platform", "darwin")


def prepare(root):
    for relative in (
        ".venv/bin/python",
        "scripts/run_policy_daily.py",
        "docs/policy-daily-task.md",
        "docs/policy-result.schema.json",
        "bin/codex",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o755 if relative in {".venv/bin/python", "bin/codex"} else 0o644)
    codex = root / "bin/codex"
    generated = root / "new.plist"
    generated.write_bytes(plistlib.dumps(installer.job_definition(root, codex)))
    target_dir = root / "agents"
    target_dir.mkdir()
    return codex, generated, target_dir


class Launchd:
    def __init__(self, root, target_dir, *, loaded=None, disabled=None):
        self.root = root
        self.target = target_dir / f"{installer.LABEL}.plist"
        self.loaded = loaded
        self.disabled = disabled
        self.calls = []
        self.fail = None
        self.fail_after_bootstrap = False
        self.running = False

    def __call__(self, command, **kwargs):
        assert command[0] == "/bin/launchctl"
        assert kwargs["timeout"] == 10
        self.calls.append(command)
        verb = command[1]
        if self.fail == verb:
            self.fail = None
            if verb == "bootstrap" and self.fail_after_bootstrap:
                self.loaded = plistlib.loads(self.target.read_bytes())
            raise subprocess.CalledProcessError(5, command)
        output, returncode = "", 0
        if verb == "print":
            if self.loaded is None:
                returncode = 113
            else:
                arguments = "\n".join(f"\t\t{arg}" for arg in self.loaded["ProgramArguments"])
                state = "running" if self.running else "not running"
                output = (
                    f"{DOMAIN}/{installer.LABEL} = {{\n\tpath = {self.target}\n"
                    f"\tstate = {state}\n"
                    f"\tprogram = {self.loaded['ProgramArguments'][0]}\n"
                    f"\targuments = {{\n{arguments}\n\t}}\n"
                    f"\tworking directory = {self.loaded['WorkingDirectory']}\n}}\n"
                )
        elif verb == "print-disabled":
            entry = (
                f'"{installer.LABEL}" => {"disabled" if self.disabled else "enabled"}\n'
                if self.disabled is not None
                else ""
            )
            output = f"disabled services = {{\n{entry}}}\n"
        elif verb == "bootout":
            self.loaded = None
        elif verb == "enable":
            self.disabled = False
        elif verb == "disable":
            self.disabled = True
        elif verb == "bootstrap":
            assert self.disabled is False
            self.loaded = plistlib.loads(self.target.read_bytes())
        else:
            pytest.fail(f"Unexpected command: {command}")
        return SimpleNamespace(returncode=returncode, stdout=output, stderr="")

    @property
    def mutations(self):
        return [call[1] for call in self.calls if call[1] not in {"print", "print-disabled"}]


def test_job_runs_daily_at_four_without_install_time_execution_or_credentials(tmp_path):
    codex = Path("/custom/codex")
    job = installer.job_definition(tmp_path, codex)
    assert job["Label"] == "cn.index-valuation.policy-daily"
    assert job["ProgramArguments"] == [
        str(tmp_path / ".venv/bin/python"),
        str(tmp_path / "scripts/run_policy_daily.py"),
        "run",
        "--codex",
        "/custom/codex",
    ]
    assert job["StartCalendarInterval"] == {"Hour": 16, "Minute": 0}
    assert job["RunAtLoad"] is False and job["KeepAlive"] is False
    assert job["StandardOutPath"].endswith("/logs/policy-daily.stdout.log")
    assert job["StandardErrorPath"].endswith("/logs/policy-daily.stderr.log")
    assert job["EnvironmentVariables"]["TZ"] == "Asia/Shanghai"
    assert set(job["EnvironmentVariables"]) == {"PYTHONUNBUFFERED", "TZ", "PATH"}


@pytest.mark.parametrize("arguments", [[], ["--generate"]])
def test_generation_is_offline_and_does_not_execute_or_send(tmp_path, monkeypatch, arguments):
    codex, _, _ = prepare(tmp_path)
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *a, **k: pytest.fail("generate must stay offline")
    )
    assert installer.main(["--root", str(tmp_path), "--codex", str(codex), *arguments]) == 0
    generated = tmp_path / f"data/scheduler/{installer.LABEL}.plist"
    assert plistlib.loads(generated.read_bytes()) == installer.job_definition(tmp_path, codex)


@pytest.mark.parametrize(
    "missing",
    [
        ".venv/bin/python",
        "scripts/run_policy_daily.py",
        "docs/policy-daily-task.md",
        "docs/policy-result.schema.json",
        "bin/codex",
    ],
)
def test_missing_runtime_file_prevents_generation_and_installation(tmp_path, monkeypatch, missing):
    codex, generated, target_dir = prepare(tmp_path)
    (tmp_path / missing).unlink()
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *a, **k: pytest.fail("must validate before launchctl")
    )
    assert installer.main(["--root", str(tmp_path), "--codex", str(codex), "--install"]) == 1
    with pytest.raises(ValueError):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("program", [".venv/bin/python", "bin/codex"])
def test_nonexecutable_runtime_is_rejected(tmp_path, program):
    codex, _, _ = prepare(tmp_path)
    (tmp_path / program).chmod(0o644)
    assert installer.main(["--root", str(tmp_path), "--codex", str(codex)]) == 1
    assert not (tmp_path / "data").exists()


def test_relative_codex_path_is_rejected(tmp_path):
    prepare(tmp_path)
    assert installer.main(["--root", str(tmp_path), "--codex", "bin/codex"]) == 1
    assert not (tmp_path / "data").exists()


def test_wrong_system_timezone_is_rejected_before_writing(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)

    class DifferentTime:
        @staticmethod
        def now():
            return SimpleNamespace(
                astimezone=lambda: RealDatetime(2026, 10, 22, tzinfo=timezone(timedelta(hours=-4)))
            )

    monkeypatch.setattr(installer, "datetime", DifferentTime)
    with pytest.raises(ValueError, match="UTC\\+8"):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert installer.main(["--root", str(tmp_path), "--codex", str(codex)]) == 1
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "damage", ["root", "label", "command", "program", "extra_argument", "symlink"]
)
def test_unrelated_plist_is_never_queried_or_changed(tmp_path, monkeypatch, damage):
    codex, generated, target_dir = prepare(tmp_path)
    target = target_dir / f"{installer.LABEL}.plist"
    job = installer.job_definition(tmp_path, codex)
    if damage == "root":
        job["WorkingDirectory"] = "/other"
    elif damage == "label":
        job["Label"] = "other.job"
    elif damage == "command":
        job["ProgramArguments"][2] = "other-command"
    elif damage == "program":
        job["Program"] = "/other/program"
    elif damage == "extra_argument":
        job["ProgramArguments"].append("--unrecognized")
    if damage == "symlink":
        target.symlink_to(generated)
    else:
        target.write_bytes(plistlib.dumps(job))
    original = target.read_bytes()
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *a, **k: pytest.fail("must not touch unrelated job")
    )
    with pytest.raises(ValueError):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    with pytest.raises(ValueError):
        installer.uninstall(tmp_path, target_dir, DOMAIN)
    assert target.read_bytes() == original


@pytest.mark.parametrize("damage", ["missing_plist", "loaded_root", "running", "loaded_command"])
def test_unverifiable_or_running_service_is_never_replaced(tmp_path, monkeypatch, damage):
    codex, generated, target_dir = prepare(tmp_path)
    job = installer.job_definition(tmp_path, codex)
    target = target_dir / f"{installer.LABEL}.plist"
    if damage != "missing_plist":
        target.write_bytes(generated.read_bytes())
    if damage == "loaded_root":
        job["WorkingDirectory"] = "/other"
    elif damage == "loaded_command":
        job["ProgramArguments"][1] = "/other/runner.py"
    launchd = Launchd(tmp_path, target_dir, loaded=job)
    launchd.running = damage == "running"
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    with pytest.raises(ValueError):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    with pytest.raises(ValueError):
        installer.uninstall(tmp_path, target_dir, DOMAIN)
    assert not launchd.mutations


def test_first_install_enables_disabled_label_and_preserves_other_jobs(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)
    other = target_dir / "cn.index-valuation.erp-sheet.plist"
    other.write_bytes(b"original ERP job")
    launchd = Launchd(tmp_path, target_dir, disabled=True)
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert launchd.loaded == installer.job_definition(tmp_path, codex)
    assert launchd.disabled is False
    assert launchd.mutations == ["enable", "bootstrap"]
    assert other.read_bytes() == b"original ERP job"
    assert not (tmp_path / "data/scheduler/backups").exists()


def test_reinstall_backs_up_unloaded_job_and_allows_codex_path_change(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)
    target = target_dir / f"{installer.LABEL}.plist"
    previous = installer.job_definition(tmp_path, Path("/old/codex"))
    previous["StartCalendarInterval"] = {"Hour": 15, "Minute": 30}
    original = plistlib.dumps(previous)
    target.write_bytes(original)
    launchd = Launchd(tmp_path, target_dir, disabled=True)
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    backups = list((tmp_path / "data/scheduler/backups").glob("policy-daily-*.plist"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert target.read_bytes() == generated.read_bytes()
    assert launchd.disabled is False


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("failure", ["enable", "bootstrap", "partial_bootstrap", "write"])
def test_failed_replacement_restores_contents_loading_and_enabled_state(
    tmp_path, monkeypatch, disabled, loaded, failure
):
    codex, generated, target_dir = prepare(tmp_path)
    target = target_dir / f"{installer.LABEL}.plist"
    previous = installer.job_definition(tmp_path, Path("/old/codex"))
    previous["StartCalendarInterval"] = {"Hour": 15, "Minute": 30}
    original = plistlib.dumps(previous)
    target.write_bytes(original)
    launchd = Launchd(tmp_path, target_dir, loaded=previous if loaded else None, disabled=disabled)
    launchd.fail = "bootstrap" if failure == "partial_bootstrap" else failure
    launchd.fail_after_bootstrap = failure == "partial_bootstrap"
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    real_write = installer._write

    def write(path, content):
        if failure == "write" and path == target and content != original:
            raise OSError("simulated atomic write failure")
        real_write(path, content)

    monkeypatch.setattr(installer, "_write", write)
    with pytest.raises((OSError, subprocess.CalledProcessError)):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert target.read_bytes() == original
    assert launchd.loaded == (previous if loaded else None)
    assert launchd.disabled is disabled
    backups = list((tmp_path / "data/scheduler/backups").glob("policy-daily-*.plist"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_failed_first_install_removes_plist_and_restores_disabled_override(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)
    launchd = Launchd(tmp_path, target_dir, disabled=True)
    launchd.fail = "bootstrap"
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert not launchd.target.exists()
    assert launchd.loaded is None
    assert launchd.disabled is True


@pytest.mark.parametrize("failed_query", ["print", "print-disabled"])
def test_unknown_launchd_state_prevents_all_mutations(tmp_path, monkeypatch, failed_query):
    codex, generated, target_dir = prepare(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == failed_query:
            return SimpleNamespace(returncode=5, stdout="unknown", stderr="permission denied")
        return SimpleNamespace(returncode=113, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(ValueError):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert all(call[1] in {"print", "print-disabled"} for call in calls)
    assert not (target_dir / f"{installer.LABEL}.plist").exists()


def test_uninstall_backs_up_only_owned_job_and_preserves_disabled_state(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)
    target = target_dir / f"{installer.LABEL}.plist"
    original = generated.read_bytes()
    target.write_bytes(original)
    other = target_dir / "cn.index-valuation.task-summary.plist"
    other.write_bytes(b"summary job")
    launchd = Launchd(tmp_path, target_dir, loaded=installer.job_definition(tmp_path, codex))
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    installer.uninstall(tmp_path, target_dir, DOMAIN)
    assert not target.exists() and launchd.loaded is None
    assert launchd.mutations == ["bootout"]
    assert other.read_bytes() == b"summary job"
    backups = list((tmp_path / "data/scheduler/backups").glob("policy-daily-*.plist"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_failed_uninstall_restores_existing_loaded_service(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)
    target = target_dir / f"{installer.LABEL}.plist"
    original = generated.read_bytes()
    target.write_bytes(original)
    previous = installer.job_definition(tmp_path, codex)
    launchd = Launchd(tmp_path, target_dir, loaded=previous, disabled=True)
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    real_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == target:
            raise OSError("simulated removal failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError):
        installer.uninstall(tmp_path, target_dir, DOMAIN)
    assert target.read_bytes() == original
    assert launchd.loaded == previous and launchd.disabled is True


def test_failed_bootout_does_not_lose_original_job(tmp_path, monkeypatch):
    codex, generated, target_dir = prepare(tmp_path)
    target = target_dir / f"{installer.LABEL}.plist"
    original = generated.read_bytes()
    target.write_bytes(original)
    previous = installer.job_definition(tmp_path, codex)
    launchd = Launchd(tmp_path, target_dir, loaded=previous, disabled=True)
    launchd.fail = "bootout"
    monkeypatch.setattr(installer.subprocess, "run", launchd)
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(tmp_path, codex, generated, target_dir, DOMAIN)
    assert target.read_bytes() == original
    assert launchd.loaded == previous and launchd.disabled is True


def test_atomic_write_preserves_old_file_and_cleans_temporary_on_replace_error(
    tmp_path, monkeypatch
):
    target = tmp_path / "job.plist"
    target.write_bytes(b"old")

    def fail(*args):
        raise OSError("disk error")

    monkeypatch.setattr(installer.os, "replace", fail)
    with pytest.raises(OSError):
        installer._write(target, b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]
