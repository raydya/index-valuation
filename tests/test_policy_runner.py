import fcntl
import importlib.util
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from index_valuation.policy_digest import AUTHORITIES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "run_policy_daily", ROOT / "scripts/run_policy_daily.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
NOW = datetime.fromisoformat("2026-10-22T16:00:00+08:00")


def prepare(root):
    (root / "docs").mkdir()
    (root / "docs/policy-daily-task.md").write_text("read-only research `literal` $(literal)")
    (root / "docs/policy-result.schema.json").write_text("{}")


def payload():
    return {
        "status": "completed",
        "summary": "No events found in checked sources.",
        "overall_impact": "uncertain",
        "major_policy_signal": False,
        "events": [],
        "coverage": [
            {
                "authority": key,
                "status": "checked",
                "urls": [
                    "https://www.gov.cn/" if key == "government" else f"https://www.{key}.gov.cn/"
                ],
                "note": "Offline fixture only.",
            }
            for key in AUTHORITIES
        ],
        "issues": [],
    }


def execution(monkeypatch, result="valid", *, status="finished", exit_code=0):
    calls, sends = [], []

    def execute(**kwargs):
        calls.append(kwargs)
        assert kwargs["research_only"] is True
        assert kwargs["timeout"] == 2400
        assert "`literal` $(literal)" in kwargs["prompt"]
        body = payload()
        if result == "partial":
            body["coverage"].pop()
        if result == "invalid":
            body["major_policy_signal"] = True
        if result != "missing":
            (kwargs["run_dir"] / "result.json").write_text(json.dumps(body))
        return {"status": status, "exit_code": exit_code}

    def deliver(_settings, text, path):
        sends.append((text, path))
        return {"status": "sent", "message_ids": [99]}

    monkeypatch.setattr(runner, "TelegramSettings", lambda **_: object())
    return execute, deliver, calls, sends


def state(root):
    return json.loads((root / "data/scheduler/policy-daily/last-execution.json").read_text())


def test_scheduled_search_freezes_evidence_and_pushes_only_once(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver) == 0
    )
    record = state(tmp_path)
    assert record["status"] == "completed"
    assert record["cutoff_at"] == "2026-10-22T16:00:00+08:00"
    base = tmp_path / "data/scheduler/policy-daily/runs/2026-10-22"
    frozen = (base / "report.json").read_bytes()
    assert json.loads((base / "state.json").read_text()) == record
    assert "在已检查来源中未发现" in sends[0][0]
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver) == 0
    )
    assert len(calls) == len(sends) == 1
    assert (base / "report.json").read_bytes() == frozen


@pytest.mark.parametrize("dry", [True, False])
def test_early_wakeup_does_not_consume_today(tmp_path, monkeypatch, dry):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    assert (
        runner.run_daily(
            tmp_path,
            Path("/codex"),
            now=NOW.replace(hour=9),
            dry_run=dry,
            executor=execute,
            delivery=deliver,
        )
        == 0
    )
    assert not calls and not sends
    assert not (tmp_path / "data").exists()


def test_preview_uses_real_earlier_cutoff_and_does_not_send_or_consume_schedule(
    tmp_path, monkeypatch
):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    assert (
        runner.run_daily(
            tmp_path,
            Path("/codex"),
            now=NOW.replace(hour=9),
            preview=True,
            executor=execute,
            delivery=deliver,
        )
        == 0
    )
    assert len(calls) == 1 and not sends
    base = tmp_path / "data/scheduler/policy-daily"
    assert not (base / "last-execution.json").exists()
    assert not (base / "runs").exists()
    report = json.loads(next((base / "previews").glob("*/report.json")).read_text())
    assert report["cutoff_at"] == "2026-10-22T09:00:00+08:00"


def test_historical_preview_keeps_actual_execution_time_and_only_backdates_search(
    tmp_path, monkeypatch
):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    assert (
        runner.run_daily(
            tmp_path,
            Path("/codex"),
            now=NOW,
            preview=True,
            report_date=date(2026, 10, 21),
            executor=execute,
            delivery=deliver,
        )
        == 0
    )
    base = tmp_path / "data/scheduler/policy-daily"
    report = json.loads(next((base / "previews").glob("*/report.json")).read_text())
    assert report["date"] == "2026-10-21"
    assert report["window_start"] == "2026-10-20T16:00:00+08:00"
    assert report["cutoff_at"] == "2026-10-21T16:00:00+08:00"
    assert report["execution"]["started_at"] == NOW.isoformat()
    assert "实际执行时间：2026-10-22" in calls[0]["prompt"]
    assert "报告日期：2026-10-21" in calls[0]["prompt"]
    assert "历史回溯试跑" in calls[0]["prompt"]
    assert not sends and not (base / "runs").exists()
    assert not (base / "last-execution.json").exists()


@pytest.mark.parametrize("preview,day", [(False, date(2026, 10, 21)), (True, date(2026, 10, 23))])
def test_historical_run_or_future_preview_cannot_create_receipts(tmp_path, preview, day):
    with pytest.raises(ValueError, match="Historical dates"):
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, preview=preview, report_date=day)
    assert not (tmp_path / "data").exists()


def test_delayed_launch_uses_1600_cutoff_not_current_time(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, _, _ = execution(monkeypatch)
    runner.run_daily(
        tmp_path, Path("/codex"), now=NOW.replace(hour=20), executor=execute, delivery=deliver
    )
    assert state(tmp_path)["cutoff_at"] == "2026-10-22T16:00:00+08:00"


def test_preview_lock_cannot_skip_the_daily_launch(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    base = tmp_path / "data/scheduler/policy-daily"
    base.mkdir(parents=True)
    with (base / "preview.lock").open("a") as preview_lock:
        fcntl.flock(preview_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert (
            runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
            == 0
        )
    assert len(calls) == len(sends) == 1
    assert state(tmp_path)["status"] == "completed"


@pytest.mark.parametrize("result", ["invalid", "missing"])
def test_exit_zero_without_valid_report_is_not_no_policy(tmp_path, monkeypatch, result):
    prepare(tmp_path)
    execute, deliver, _, sends = execution(monkeypatch, result)
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver) == 1
    )
    assert state(tmp_path)["status"] == "failed"
    assert "无法可靠判断" in sends[0][0]
    assert "未发现" not in sends[0][0]


def test_incomplete_coverage_is_sent_as_partial(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, _, sends = execution(monkeypatch, "partial")
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver) == 2
    )
    assert state(tmp_path)["status"] == "partial"
    assert "已检查 7/8" in sends[0][0]


@pytest.mark.parametrize("status,code", [("failed", 1), ("timed_out", 124), ("interrupted", 130)])
def test_process_failure_sends_an_explicit_failure_receipt(tmp_path, monkeypatch, status, code):
    prepare(tmp_path)
    execute, deliver, _, sends = execution(monkeypatch, status=status, exit_code=code)
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
        == code
    )
    assert state(tmp_path)["status"] == status
    assert "无法可靠判断" in sends[0][0]


def test_delivery_failure_preserves_business_result_and_retry_does_not_research(
    tmp_path, monkeypatch
):
    prepare(tmp_path)
    execute, _, calls, _ = execution(monkeypatch)
    assert (
        runner.run_daily(
            tmp_path,
            Path("/codex"),
            now=NOW,
            executor=execute,
            delivery=lambda *_: {"status": "delivery_unknown"},
        )
        == 2
    )
    record = state(tmp_path)
    assert record["status"] == "completed"
    assert record["notification"]["status"] == "delivery_unknown"
    seen = []

    def retry(_settings, text, _path):
        seen.append(text)
        return {"status": "delivery_unknown"}

    assert runner.retry_delivery(tmp_path, NOW.date(), now=NOW, delivery=retry) == 2
    assert seen == [record["result_text"]]
    assert len(calls) == 1


def test_retry_rejects_changed_frozen_content(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, _, _ = execution(monkeypatch)
    runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
    path = tmp_path / "data/scheduler/policy-daily/runs/2026-10-22/report.json"
    report = json.loads(path.read_text())
    report["text"] = "changed"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Frozen policy"):
        runner.retry_delivery(tmp_path, NOW.date(), now=NOW, delivery=deliver)


def test_abandoned_run_records_interruption_without_another_search(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, calls, _ = execution(monkeypatch)
    runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
    path = tmp_path / "data/scheduler/policy-daily/runs/2026-10-22/state.json"
    record = json.loads(path.read_text())
    record.update(status="running", finished_at=None, exit_code=None)
    path.write_text(json.dumps(record))
    (path.parent / "report.json").unlink()
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
        == 130
    )
    assert state(tmp_path)["status"] == "interrupted"
    assert len(calls) == 1


def test_crash_after_freezing_restores_valid_research_without_researching(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
    path = tmp_path / "data/scheduler/policy-daily/runs/2026-10-22/state.json"
    record = json.loads(path.read_text())
    original = dict(record)
    record.update(status="running", finished_at=None, exit_code=None)
    path.write_text(json.dumps(record))
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver) == 0
    )
    recovered = state(tmp_path)
    assert recovered["status"] == "completed"
    assert recovered["result_text"] == original["result_text"] == sends[-1][0]
    assert recovered["summary"] == original["summary"]
    assert len(calls) == 1


def test_next_day_retry_recovers_frozen_report_after_crash(tmp_path, monkeypatch):
    prepare(tmp_path)
    execute, deliver, calls, sends = execution(monkeypatch)
    runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver)
    path = tmp_path / "data/scheduler/policy-daily/runs/2026-10-22/state.json"
    record = json.loads(path.read_text())
    record.update(status="running", finished_at=None, exit_code=None, result_text="")
    path.write_text(json.dumps(record))
    assert (
        runner.retry_delivery(tmp_path, NOW.date(), now=NOW.replace(day=23), delivery=deliver) == 0
    )
    recovered = json.loads(path.read_text())
    assert recovered["status"] == "completed"
    assert recovered["result_text"] == sends[-1][0]
    assert len(calls) == 1


@pytest.mark.parametrize("after_send", [False, True])
def test_transient_receipt_error_after_freeze_never_invalidates_report(
    tmp_path, monkeypatch, after_send
):
    prepare(tmp_path)
    execute, deliver, calls, _ = execution(monkeypatch)
    real_save = runner.save_json
    injected = False

    def save(path, content):
        nonlocal injected
        if (
            path.name == "state.json"
            and content.get("status") == "completed"
            and (not after_send or content.get("notification", {}).get("status") == "sent")
            and not injected
        ):
            injected = True
            raise OSError("transient disk error")
        real_save(path, content)

    monkeypatch.setattr(runner, "save_json", save)
    assert (
        runner.run_daily(tmp_path, Path("/codex"), now=NOW, executor=execute, delivery=deliver) == 1
    )
    assert state(tmp_path)["status"] == "completed"
    assert runner.retry_delivery(tmp_path, NOW.date(), now=NOW, delivery=deliver) == 0
    assert len(calls) == 1
