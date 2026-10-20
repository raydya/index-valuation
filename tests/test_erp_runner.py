import importlib.util
import json
import signal
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_erp_sync", ROOT / "scripts/run_erp_sync.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
NOW = datetime.fromisoformat("2026-10-20T18:00:00+08:00")


def prepare(root):
    (root / "docs").mkdir()
    (root / "docs/erp-sheet-update-task.md").write_text("Google Drive only\n`literal` $(literal)")
    (root / "docs/erp-result.schema.json").write_text("{}")


def payload():
    return {
        "status": "partial",
        "readback_verified": True,
        "summary": "已回读，恒生科技日期仍未知",
        "issues": ["恒生科技未核验"],
        "data_items": [
            {
                "name": "恒生科技",
                "date": None,
                "complete_date": None,
                "status": "unknown",
                "note": "保留旧值",
            }
        ],
        "result_text": "表格已回读验证，有未核验项。",
    }


def child_factory(monkeypatch, result="valid", exit_code=0):
    calls, signals = [], []

    class Child:
        pid = 12345
        returncode = exit_code

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            self.output = Path(command[command.index("--output-last-message") + 1])

        def communicate(self, *, input, timeout):
            assert b"`literal` $(literal)" in input
            assert timeout == 3600
            if result == "timeout":
                raise subprocess.TimeoutExpired(calls[0][0], timeout)
            if result == "interrupt":
                raise KeyboardInterrupt
            if result != "missing":
                body = payload() if result == "valid" else {**payload(), "readback_verified": False}
                self.output.write_text(json.dumps(body))

        def poll(self):
            return None

        def wait(self, timeout):
            return 0

    from index_valuation import codex_runner

    monkeypatch.setattr(codex_runner.subprocess, "Popen", Child)
    monkeypatch.setattr(codex_runner.os, "killpg", lambda *args: signals.append(args))
    return calls, signals


def test_execution_creates_a_matched_business_receipt_instead_of_queue_only(tmp_path, monkeypatch):
    prepare(tmp_path)
    calls, _ = child_factory(monkeypatch)
    assert runner.run_update(tmp_path, Path("/path with spaces/codex"), now=NOW) == 2
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:4] == ["/path with spaces/codex", "--no-daemon", "exec", "--approve-for-me"]
    assert "queue" not in command and not kwargs.get("shell")
    assert kwargs["start_new_session"] is True
    base = tmp_path / "data/scheduler/erp-sheet"
    state = json.loads((base / "last-execution.json").read_text())
    receipt = json.loads((base / "results/2026-10-20.json").read_text())
    assert state["status"] == "partial" and receipt["readback_verified"]
    assert state["run_id"] == receipt["run_id"]
    assert not (base / "last-queued.json").exists()


@pytest.mark.parametrize("result", ["missing", "unverified"])
def test_exit_zero_without_valid_readback_is_failure_and_is_not_automatically_retried(
    tmp_path, monkeypatch, result
):
    prepare(tmp_path)
    calls, _ = child_factory(monkeypatch, result)
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == 1
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == 0
    assert len(calls) == 1
    base = tmp_path / "data/scheduler/erp-sheet"
    receipt = json.loads((base / "results/2026-10-20.json").read_text())
    assert receipt["status"] == "failed" and not receipt["readback_verified"]
    assert "不能确认" in receipt["summary"]


@pytest.mark.parametrize(
    "result,code,status",
    [("timeout", 124, "timed_out"), ("interrupt", 130, "interrupted"), ("valid", 3, "failed")],
)
def test_timeout_interrupt_and_process_failure_are_terminal_and_preserve_uncertainty(
    tmp_path, monkeypatch, result, code, status
):
    prepare(tmp_path)
    _, signals = child_factory(monkeypatch, result, exit_code=3 if code == 3 else 0)
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == code
    base = tmp_path / "data/scheduler/erp-sheet"
    state = json.loads((base / "last-execution.json").read_text())
    assert state["status"] == status and state["finished_at"]
    receipt = json.loads((base / "results/2026-10-20.json").read_text())
    assert receipt["status"] == "failed"
    if result in {"timeout", "interrupt"}:
        assert signals[0] == (12345, signal.SIGTERM)
        assert (12345, signal.SIGKILL) in signals


def test_output_persistence_failure_does_not_leave_running(tmp_path, monkeypatch):
    prepare(tmp_path)
    child_factory(monkeypatch)
    monkeypatch.setattr(runner, "save_result", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == 1
    state = json.loads((tmp_path / "data/scheduler/erp-sheet/last-execution.json").read_text())
    assert state["status"] == "failed" and state["finished_at"]


def test_abandoned_same_day_attempt_is_closed_without_reexecuting(tmp_path, monkeypatch):
    prepare(tmp_path)
    base = tmp_path / "data/scheduler/erp-sheet"
    run = base / "runs/2026-10-20"
    run.mkdir(parents=True)
    state = {"status": "running", "date": "2026-10-20", "run_id": "original"}
    (run / "state.json").write_text(json.dumps(state))
    monkeypatch.setattr(runner, "execute_codex", lambda **_: pytest.fail("must not repeat"))
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == 1
    updated = json.loads((base / "last-execution.json").read_text())
    assert updated["status"] == "interrupted" and updated["run_id"] == "original"


def test_early_and_dry_run_do_not_create_execution_or_consume_today(tmp_path, monkeypatch):
    prepare(tmp_path)
    monkeypatch.setattr(runner, "execute_codex", lambda **_: pytest.fail("must not start"))
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW.replace(hour=7)) == 0
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW, dry_run=True) == 0
    assert not (tmp_path / "data/scheduler/erp-sheet").exists()


def test_prior_queue_does_not_prevent_new_execution_and_is_preserved(tmp_path, monkeypatch):
    prepare(tmp_path)
    base = tmp_path / "data/scheduler/erp-sheet"
    base.mkdir(parents=True)
    queued = {"date": NOW.date().isoformat(), "status": "queued", "thread": "old-thread"}
    (base / "last-queued.json").write_text(json.dumps(queued))
    child_factory(monkeypatch)
    assert runner.run_update(tmp_path, Path("/codex"), now=NOW) == 2
    assert json.loads((base / "last-queued.json").read_text()) == queued
