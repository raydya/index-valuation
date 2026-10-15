import importlib.util
import json
import plistlib
import subprocess
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "portfolio_schedule", ROOT / "scripts/portfolio_schedule.py"
)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)
START = date(2026, 10, 16)


def test_daily_calendar_removes_one_time_constraints_and_keeps_market_times(tmp_path):
    jobs = schedule.definitions(tmp_path, Path("/codex"), START)
    assert [j["StartCalendarInterval"] for j in jobs] == [
        {"Hour": 9, "Minute": 0},
        {"Hour": 17, "Minute": 0},
    ]
    assert all(not j.get("LaunchOnlyOnce") and not j["RunAtLoad"] for j in jobs)
    assert len({j["StandardOutPath"] for j in jobs}) == 2


@pytest.mark.parametrize(
    "when,hour,attempted,expected",
    [
        ("2026-10-15T18:00:00+08:00", 17, False, "not_started"),
        ("2026-10-16T08:59:59+08:00", 9, False, "not_due"),
        ("2026-10-16T09:00:00+08:00", 9, False, "due"),
        ("2026-10-16T17:00:00+08:00", 17, True, "already_attempted"),
        ("2026-10-17T17:00:00+08:00", 17, False, "due"),
        ("2026-10-18T09:00:00+08:00", 9, False, "due"),
    ],
)
def test_start_date_time_daily_duplicate_and_weekend_rules(when, hour, attempted, expected):
    assert schedule.eligibility(datetime.fromisoformat(when), START, hour, attempted) == expected


@pytest.mark.parametrize("result", [0, 2, "timeout"])
def test_each_day_runs_independently_and_writes_only_process_outcome(tmp_path, monkeypatch, result):
    (tmp_path / "docs").mkdir()
    (tmp_path / schedule.JOBS["us"]["prompt"]).write_text("Google Drive only\n$(literal)")
    calls, signals = [], []

    class Child:
        returncode = result
        pid = 12345

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

        def communicate(self, *, input, timeout):
            assert b"Google Drive only\n$(literal)" in input
            assert timeout == 3600
            if result == "timeout":
                raise subprocess.TimeoutExpired(calls[-1][0], timeout)

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(schedule.subprocess, "Popen", Child)
    monkeypatch.setattr(schedule.os, "killpg", lambda *args: signals.append(args))
    monkeypatch.setattr(schedule.os, "umask", lambda *_: None)
    first = datetime.fromisoformat("2026-10-16T09:00:00+08:00")
    second = datetime.fromisoformat("2026-10-17T09:00:00+08:00")
    expected = 124 if result == "timeout" else result
    assert schedule.run_job(tmp_path, "us", Path("/codex"), START, now=first) == expected
    assert schedule.run_job(tmp_path, "us", Path("/codex"), START, now=first) == 0
    assert schedule.run_job(tmp_path, "us", Path("/codex"), START, now=second) == expected
    assert len(calls) == 2
    assert not calls[0][1].get("shell")
    base = tmp_path / "data/scheduled/portfolio-daily"
    for day in ["2026-10-16", "2026-10-17"]:
        state = json.loads((base / day / "us.state.json").read_text())
        assert state["date"] == day and state["exit_code"] == expected
        assert state["status"] in ("process_finished", "process_failed", "process_timed_out")
    assert bool(signals) == (result == "timeout")


def test_dry_run_has_no_run_records_or_external_execution(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / schedule.JOBS["us"]["prompt"]).write_text("Read the live sheet.")

    def unexpected(*args, **kwargs):
        pytest.fail("Dry run must not start Codex")

    monkeypatch.setattr(schedule.subprocess, "Popen", unexpected)
    assert (
        schedule.run_job(
            tmp_path,
            "us",
            Path("/codex"),
            START,
            dry_run=True,
            now=datetime.fromisoformat("2026-10-16T09:00:00+08:00"),
        )
        == 0
    )
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("fail_second", [False, True])
def test_migration_retires_old_jobs_only_when_both_daily_jobs_load(
    tmp_path, monkeypatch, fail_second
):
    targets = tmp_path / "LaunchAgents"
    targets.mkdir()
    old_bytes, active, calls = {}, set(), []
    for job in schedule.JOBS.values():
        payload = {
            "Label": job["legacy"],
            "WorkingDirectory": str(tmp_path),
            "ProgramArguments": [
                str(tmp_path / ".venv/bin/python"),
                str(tmp_path / "data/scheduled/portfolio-2026-10-16/run_once.py"),
            ],
        }
        data = plistlib.dumps(payload)
        old_bytes[job["legacy"]] = data
        (targets / f"{job['legacy']}.plist").write_bytes(data)
        active.add(job["legacy"])
    unrelated = targets / "unrelated.plist"
    unrelated.write_bytes(b"leave unchanged")
    generated = []
    for job in schedule.definitions(tmp_path, Path("/codex"), START):
        source = tmp_path / f"{job['Label']}.plist"
        source.write_bytes(plistlib.dumps(job))
        generated.append(source)

    def run(command, **kwargs):
        action = command[1]
        label = Path(command[-1]).stem if action == "bootstrap" else command[-1].split("/")[-1]
        calls.append((action, label))
        if action == "print":
            return SimpleNamespace(
                returncode=0 if label in active else 3, stdout="\n\tstate = not running\n"
            )
        if action == "bootstrap":
            if fail_second and label == schedule.JOBS["cn_hk_funds"]["label"]:
                raise subprocess.CalledProcessError(5, command)
            active.add(label)
        elif action == "bootout":
            active.discard(label)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(schedule.subprocess, "run", run)
    if fail_second:
        with pytest.raises(subprocess.CalledProcessError):
            schedule.install_jobs(tmp_path, generated, targets, "gui/501")
        assert active == set(old_bytes)
        for label, data in old_bytes.items():
            assert (targets / f"{label}.plist").read_bytes() == data
        assert not any((targets / p.name).exists() for p in generated)
    else:
        schedule.install_jobs(tmp_path, generated, targets, "gui/501")
        assert active == {j["label"] for j in schedule.JOBS.values()}
        assert all(not (targets / f"{label}.plist").exists() for label in old_bytes)
        last_new = max(i for i, (action, _) in enumerate(calls) if action == "bootstrap")
        first_old = min(
            i
            for i, (action, label) in enumerate(calls)
            if action == "bootout" and label in old_bytes
        )
        assert first_old > last_new
    assert unrelated.read_bytes() == b"leave unchanged"


def test_installer_refuses_different_checkout_before_any_mutation(tmp_path, monkeypatch):
    targets = tmp_path / "LaunchAgents"
    targets.mkdir()
    generated = []
    for job in schedule.definitions(tmp_path, Path("/codex"), START):
        path = tmp_path / f"{job['Label']}.plist"
        path.write_bytes(plistlib.dumps(job))
        generated.append(path)
    original = plistlib.dumps({"WorkingDirectory": "/elsewhere", "ProgramArguments": []})
    (targets / generated[0].name).write_bytes(original)

    def run(command, **kwargs):
        assert command[1] == "print"
        return SimpleNamespace(returncode=3, stdout="")

    monkeypatch.setattr(schedule.subprocess, "run", run)
    with pytest.raises(ValueError, match="拒绝覆盖"):
        schedule.install_jobs(tmp_path, generated, targets, "gui/501")
    assert (targets / generated[0].name).read_bytes() == original
