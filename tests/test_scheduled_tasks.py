import json
import os
import plistlib
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from index_valuation.domain import IndexSpec
from index_valuation.erp_results import EXPECTED_ERP_ITEMS
from index_valuation.scheduled_tasks import (
    MAX_SCHEDULER_SNAPSHOT,
    TASKS,
    ZONE,
    ScheduledTasks,
    write_scheduler_snapshot,
)
from index_valuation.webapp import create_app

NOW = datetime(2026, 10, 16, 16, 0, tzinfo=ZONE)
SPEC = IndexSpec("us", "测试美股", "US", "US.INDEX")


class ReadOnlyRepository:
    def __init__(self):
        self.summaries = {}
        self.records = {}
        self.requested_ids = []

    def scheduled_index_summaries(self, specs):
        return self.summaries

    def scheduled_run_records(self, ids):
        self.requested_ids.extend(ids)
        return [self.records[key] for key in ids if key in self.records]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def service(tmp_path):
    root, home = tmp_path / "project", tmp_path / "home"
    root.mkdir()
    launch_dir = home / "Library/LaunchAgents"
    launch_dir.mkdir(parents=True)
    for task in TASKS:
        hour = {"erp": 8, "policy_daily": 16}.get(task.id, 9 if task.id.endswith("us") else 17)
        scope = (
            ["--market", *task.markets]
            if task.markets
            else (
                ["run", task.portfolio_key]
                if task.portfolio_key
                else ["run"]
                if task.id == "policy_daily"
                else []
            )
        )
        (launch_dir / f"{task.label}.plist").write_bytes(
            plistlib.dumps(
                {
                    "Label": task.label,
                    "WorkingDirectory": str(root),
                    "ProgramArguments": [
                        str(root / ".venv/bin/python"),
                        str(root / "scripts" / task.script),
                        *scope,
                    ],
                    "StartCalendarInterval": {"Hour": hour, "Minute": 0},
                    "EnvironmentVariables": {"SECRET": "must-not-expose"},
                }
            )
        )

    def launchctl(label):
        assert label in {task.label for task in TASKS}
        return SimpleNamespace(returncode=0, stdout="state = not running\nruns = 0\n", stderr="")

    return ScheduledTasks(
        ReadOnlyRepository(),
        {SPEC.key: SPEC},
        root=root,
        home=home,
        now=lambda: NOW,
        launchctl=launchctl,
    )


def task(service, key):
    return next(item for item in service.snapshot()["tasks"] if item["id"] == key)


def queued(service, **updates):
    queue = {
        "status": "queued",
        "date": "2026-10-16",
        "thread": str(uuid4()),
        "queued_at": "2026-10-16T08:12:00+08:00",
    }
    queue.update(updates)
    write_json(service.root / "data/scheduler/erp-sheet/last-queued.json", queue)
    return queue


def completed(service, queue, **updates):
    receipt = {
        **queue,
        "task": "erp",
        "status": "completed",
        "readback_verified": True,
        "started_at": "2026-10-16T14:20:00+08:00",
        "finished_at": "2026-10-16T14:29:00+08:00",
        "summary": "已回读核对",
        "result_text": "本次结果",
        "issues": [],
        "data_items": [
            {
                "name": "测试指数",
                "date": "2026-10-15",
                "complete_date": "2026-10-15",
                "status": "available",
                "note": "已回读",
            }
        ],
    }
    receipt.update(updates)
    write_json(service.root / "data/scheduler/erp-sheet/results/2026-10-16.json", receipt)
    return receipt


def portfolio(service, date="2026-10-16", **updates):
    receipt = {
        "job": "us",
        "date": date,
        "started_at": f"{date}T09:03:00+08:00",
        "finished_at": f"{date}T09:15:00+08:00",
        "status": "process_finished",
        "exit_code": 0,
    }
    receipt.update(updates)
    base = service.root / f"data/scheduled/portfolio-daily/{date}"
    write_json(base / "us.state.json", receipt)
    return base


def test_installed_schedule_has_real_next_occurrence_and_no_run_or_environment(service):
    data = service.snapshot()
    assert len(data["tasks"]) == 6
    assert data["checked_at"] == NOW.isoformat()
    by_id = {item["id"]: item for item in data["tasks"]}
    assert by_id["index_asia"]["next_run_at"] == "2026-10-16T17:00:00+08:00"
    assert by_id["index_us"]["next_run_at"] == "2026-10-17T09:00:00+08:00"
    assert by_id["portfolio_us"]["last_run"]["status"] == "never_run"
    assert by_id["policy_daily"]["last_run"]["status"] == "never_run"
    assert by_id["policy_daily"]["next_run_at"] == "2026-10-17T16:00:00+08:00"
    assert "must-not-expose" not in json.dumps(data)


def test_midnight_schedule_uses_next_day_at_exact_due_time(service):
    path = service.home / "Library/LaunchAgents/cn.index-valuation.erp-sheet.plist"
    payload = plistlib.loads(path.read_bytes())
    payload["StartCalendarInterval"] = {"Hour": 0, "Minute": 0}
    path.write_bytes(plistlib.dumps(payload))
    service.now = lambda: datetime(2026, 12, 31, 0, 0, tzinfo=ZONE)
    assert task(service, "erp")["next_run_at"] == "2027-01-01T00:00:00+08:00"


def policy_receipt(service, **updates):
    receipt = {
        "schema": "policy_execution_v1",
        "task": "policy_daily",
        "date": "2026-10-16",
        "run_id": str(uuid4()),
        "status": "completed",
        "started_at": "2026-10-16T15:40:00+08:00",
        "finished_at": "2026-10-16T15:55:00+08:00",
        "exit_code": 0,
        "summary": "已检索主要官方来源，政策影响偏正面。",
        "result_text": "政策事实：新增资金工具。\n分析判断：有利于流动性；需跟进实施。",
        "issues": [],
        "notification": {"status": "sent", "message_ids": [123], "token": "private"},
    }
    receipt.update(updates)
    base = service.root / "data/scheduler/policy-daily"
    write_json(base / "last-execution.json", receipt)
    write_json(base / "runs/2026-10-16/state.json", receipt)
    return receipt


def test_policy_reads_matched_receipts_without_raw_model_log_or_notification_secrets(service):
    receipt = policy_receipt(service)
    base = service.root / "data/scheduler/policy-daily/runs/2026-10-16"
    (base / "events.jsonl").write_text("model-private-log")
    (base / "report.json").write_text("not-a-valid-report")
    item = task(service, "policy_daily")
    assert item["last_run"]["status"] == "completed"
    assert item["last_run"]["result_text"] == receipt["result_text"]
    assert item["last_run"]["notification_status"] == "sent"
    assert item["data_items"] == []
    assert "private" not in json.dumps(item)
    assert "not-a-valid-report" not in json.dumps(item)


@pytest.mark.parametrize(
    "updates",
    [
        {"task": "erp"},
        {"schema": "other"},
        {"date": "2026-10-15"},
        {"run_id": "invalid-id"},
        {"started_at": "2026-10-17T16:00:00+08:00"},
        {"finished_at": "2026-10-16T15:39:00+08:00"},
        {"finished_at": "2026-10-16T16:01:00+08:00"},
        {"finished_at": None},
        {"exit_code": None},
        {"exit_code": 1},
        {"summary": ""},
        {"result_text": ""},
        {"status": "partial", "exit_code": 0},
        {"status": "running", "exit_code": None},
    ],
)
def test_invalid_policy_receipt_never_becomes_completed(service, updates):
    policy_receipt(service, **updates)
    item = task(service, "policy_daily")
    assert item["last_run"]["status"] == "unknown"
    assert item["warnings"]
    assert task(service, "portfolio_us")["last_run"]["status"] == "never_run"


@pytest.mark.parametrize("damage", ["missing", "run_id", "status", "corrupt"])
def test_policy_last_execution_requires_matching_dated_state(service, damage):
    receipt = policy_receipt(service)
    path = service.root / "data/scheduler/policy-daily/runs/2026-10-16/state.json"
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("broken")
    else:
        receipt[damage] = "different"
        write_json(path, receipt)
    assert task(service, "policy_daily")["last_run"]["status"] == "unknown"


@pytest.mark.parametrize(
    "status,code", [("partial", 2), ("failed", 1), ("timed_out", 124), ("interrupted", 130)]
)
def test_policy_terminal_failure_does_not_reuse_older_success(service, status, code):
    policy_receipt(service, status=status, exit_code=code, issues=["官方来源未完整取得"])
    item = task(service, "policy_daily")
    assert item["last_run"]["status"] == status
    assert item["last_run"]["issues"] == ["官方来源未完整取得"]


def test_erp_queue_is_not_completion_even_when_submit_process_exited_zero(service):
    queued(service)
    service.launchctl = lambda _: SimpleNamespace(
        returncode=0, stdout="state = not running\nruns = 1\nlast exit code = 0\n", stderr=""
    )
    item = task(service, "erp")
    assert item["last_run"]["status"] == "queued"
    assert item["last_run"]["finished_at"] is None
    assert any("等待 7 小时" in warning for warning in item["warnings"])


def test_delayed_erp_completion_retains_queue_start_finish_and_partial_result(service):
    queue = queued(service)
    completed(
        service,
        queue,
        status="partial",
        issues=["恒生科技 PE 尚未核实"],
        data_items=[{"name": "恒生科技", "date": None, "status": "partial", "note": "PE 未核实"}],
    )
    item = task(service, "erp")
    assert item["last_run"]["status"] == "partial"
    assert item["last_run"]["queued_at"] == queue["queued_at"]
    assert item["last_run"]["started_at"] == "2026-10-16T14:20:00+08:00"
    assert item["last_run"]["finished_at"] == "2026-10-16T14:29:00+08:00"
    assert item["data_items"][0]["date"] is None
    assert not any("等待" in warning for warning in item["warnings"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread", "different-thread"),
        ("queued_at", "2026-10-16T08:11:00+08:00"),
        ("date", "2026-10-15"),
        ("task", "portfolio_us"),
    ],
)
def test_erp_receipt_requires_exact_queue_identity(service, field, value):
    queue = queued(service)
    completed(service, queue, **{field: value})
    item = task(service, "erp")
    assert item["last_run"]["status"] == "queued"
    assert not item["data_items"]


@pytest.mark.parametrize("status", ["failed", "timed_out"])
def test_newer_erp_submission_attempt_supersedes_prior_complete_result(service, status):
    queue = queued(service)
    completed(service, queue)
    write_json(
        service.root / "data/scheduler/erp-sheet/last-attempt.json",
        {
            "status": status,
            "queued_at": "2026-10-17T08:00:00+08:00",
            "started_at": "2026-10-17T08:00:00+08:00",
            "finished_at": "2026-10-17T08:01:00+08:00",
            "summary": "提交未完成",
            "exit_code": 124 if status == "timed_out" else 1,
        },
    )
    item = task(service, "erp")
    assert item["last_run"]["status"] == status
    assert not item["data_items"]


def test_portfolio_process_result_is_finished_not_verified_complete_and_plain_text(service):
    base = portfolio(service, result_file="/etc/passwd", error="secret-token")
    result = "已更新 6 项\n<script>alert('unsafe')</script>"
    (base / "us.result.md").write_text(result)
    item = task(service, "portfolio_us")
    assert item["last_run"]["status"] == "finished"
    assert item["last_run"]["result_text"] == result
    assert "secret-token" not in json.dumps(item)


def test_newer_portfolio_attempt_does_not_show_prior_finished_result(service):
    old = portfolio(service, date="2026-10-15")
    (old / "us.result.md").write_text("昨日成功")
    portfolio(service, status="attempt_started", finished_at=None, exit_code=None)
    service.launchctl = lambda _: SimpleNamespace(
        returncode=0, stdout="state = running\n", stderr=""
    )
    item = task(service, "portfolio_us")
    assert item["last_run"]["status"] == "running"
    assert item["last_run"]["started_at"] == "2026-10-16T09:03:00+08:00"
    assert not item["last_run"]["result_text"]


@pytest.mark.parametrize(
    "status,expected",
    [
        ("process_failed", "failed"),
        ("runner_failed", "failed"),
        ("process_timed_out", "timed_out"),
        ("process_finished", "unknown"),
    ],
)
def test_portfolio_failures_and_missing_result_are_not_success(service, status, expected):
    portfolio(service, status=status)
    assert task(service, "portfolio_us")["last_run"]["status"] == expected


def test_corrupt_newest_portfolio_state_does_not_fall_back_to_previous_success(service):
    old = portfolio(service, date="2026-10-15")
    (old / "us.result.md").write_text("昨日成功")
    latest = portfolio(service)
    (latest / "us.state.json").write_text("not-json")
    assert task(service, "portfolio_us")["last_run"]["status"] == "unknown"


def test_legacy_index_log_uses_last_batch_and_only_explicit_pipeline_ids(service):
    old, run, collect = (str(uuid4()) for _ in range(3))
    log = service.root / "logs/daily.us.stdout.log"
    log.parent.mkdir()
    first = [{"mode": "direct_index", "index": "us", "run_id": old, "status": "collected"}]
    last = [
        {
            "mode": "direct_index",
            "index": "us",
            "run_id": run,
            "status": "partial",
            "collection": {"run_id": collect, "sources": {"reference": {"status": "failed"}}},
        }
    ]
    log.write_text(json.dumps(first, indent=2) + "\ndiagnostics\n" + json.dumps(last, indent=2))
    service.repository.records = {
        run: {
            "index_key": "us",
            "started_at": "2026-10-16T09:12:00+08:00",
            "finished_at": "2026-10-16T09:13:00+08:00",
            "report": last[0],
        },
        collect: {
            "index_key": "us",
            "started_at": "2026-10-16T09:00:00+08:00",
            "finished_at": "2026-10-16T09:12:00+08:00",
            "report": {},
        },
    }
    service.repository.summaries = {
        "us": {
            "latest": {"trade_date": "2026-10-16", "implied_eps": 1, "details": {}},
            "complete_date": "2026-10-16",
            "processing": {
                "started_at": "2026-10-16T15:00:00+08:00",
                "status": "collected",
                "report": {},
            },
        }
    }
    item = task(service, "index_us")
    assert item["last_run"]["status"] == "partial"
    assert item["last_run"]["started_at"] == "2026-10-16T09:00:00+08:00"
    assert set(service.repository.requested_ids) == {run, collect}
    assert old not in service.repository.requested_ids
    assert item["data_items"][0]["date"] == "2026-10-16"
    assert "手动更新" in item["data_items"][0]["note"]
    assert "reference" in item["last_run"]["issues"][0]


def test_structured_index_receipt_wins_over_old_logs_and_preserves_paused_data(service):
    write_json(
        service.root / "data/scheduler/index-runs/us.latest.json",
        {
            "status": "partial",
            "markets": ["US"],
            "started_at": "2026-10-16T09:00:00+08:00",
            "finished_at": "2026-10-16T09:10:00+08:00",
            "exit_code": 2,
            "summary": "本轮部分完成",
            "run_ids": [],
        },
    )
    service.specs = {
        SPEC.key: replace(SPEC, collection_enabled=False, collection_disabled_reason="映射未核实")
    }
    item = task(service, "index_us")
    assert item["last_run"]["status"] == "partial"
    assert item["data_items"][0]["status"] == "paused"
    assert "映射未核实" in item["data_items"][0]["note"]


def test_corrupt_receipts_plist_and_database_failure_are_isolated_without_exception_leak(service):
    queue = queued(service)
    completed(service, queue)
    path = service.root / "data/scheduler/index-runs/us.latest.json"
    path.parent.mkdir(parents=True)
    path.write_text("invalid")
    (service.home / "Library/LaunchAgents/cn.index-valuation.daily.asia.plist").write_text(
        "invalid"
    )

    def broken(_):
        raise RuntimeError("password=do-not-expose <script>bad</script>")

    service.repository.scheduled_index_summaries = broken
    data = service.snapshot()
    by_id = {item["id"]: item for item in data["tasks"]}
    assert by_id["erp"]["last_run"]["status"] == "completed"
    assert by_id["index_us"]["last_run"]["status"] == "unknown"
    assert by_id["index_asia"]["scheduler"]["state"] == "unknown"
    assert data["warnings"]
    assert "do-not-expose" not in json.dumps(data)


def test_launchctl_timeout_does_not_hide_saved_results(service):
    queue = queued(service)
    completed(service, queue)

    def timeout(_):
        raise subprocess.TimeoutExpired("launchctl", 2)

    service.launchctl = timeout
    item = task(service, "erp")
    assert item["scheduler"]["state"] == "unknown"
    assert item["last_run"]["status"] == "completed"


def test_unknown_or_missing_plist_does_not_use_other_project_or_expose_it(service):
    path = service.home / "Library/LaunchAgents/cn.index-valuation.erp-sheet.plist"
    payload = plistlib.loads(path.read_bytes())
    payload["WorkingDirectory"] = "/private/other-project"
    path.write_bytes(plistlib.dumps(payload))
    item = task(service, "erp")
    assert item["scheduler"]["state"] == "unknown"
    assert "/private/other-project" not in json.dumps(item)
    path.unlink()
    service.launchctl = lambda _: SimpleNamespace(
        returncode=113, stdout="", stderr="Could not find service"
    )
    assert task(service, "erp")["scheduler"]["state"] == "not_installed"
    assert task(service, "index_us")["scheduler"]["state"] == "disabled"


def test_symlink_receipt_cannot_read_outside_task_directory(service, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret":"must-not-expose"}')
    path = service.root / "data/scheduler/erp-sheet/last-queued.json"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    item = task(service, "erp")
    assert item["last_run"]["status"] == "unknown"
    assert "must-not-expose" not in json.dumps(item)


def test_endpoint_is_read_only_refreshes_receipts_and_only_returns_json(service):
    app = create_app(repository=service.repository, specs=service.specs, scheduled_tasks=service)
    with TestClient(app) as client:
        first = client.get("/api/scheduled-tasks")
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("application/json")
        assert client.post("/api/scheduled-tasks").status_code == 405
        queued(service)
        second = client.get("/api/scheduled-tasks").json()
        assert (
            next(t for t in second["tasks"] if t["id"] == "erp")["last_run"]["status"] == "queued"
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"readback_verified": False},
        {"started_at": "2026-10-16T08:00:00+08:00"},
        {"finished_at": "2026-10-16T17:00:00+08:00"},
        {"finished_at": "2026-10-16T14:29:00"},
        {"data_items": []},
        {"issues": ["尚未核实"]},
        {"data_items": [{"name": "测试指数", "date": None, "status": "partial"}]},
    ],
)
def test_invalid_completion_evidence_never_renders_completed(service, updates):
    queue = queued(service)
    completed(service, queue, **updates)
    assert task(service, "erp")["last_run"]["status"] == "unknown"


def test_corrupt_latest_erp_attempt_supersedes_prior_completed_result(service):
    queue = queued(service)
    completed(service, queue)
    (service.root / "data/scheduler/erp-sheet/last-attempt.json").write_text("invalid")
    item = task(service, "erp")
    assert item["last_run"]["status"] == "unknown"
    assert not item["data_items"]


def manual_erp_recovery(service, *, status="partial"):
    execution = {
        "schema": "erp_execution_v1",
        "task": "erp",
        "mode": "manual_recovery",
        "execution_origin": "authorized_manual_recovery",
        "recovery_of_run_id": str(uuid4()),
        "date": "2026-10-16",
        "run_id": str(uuid4()),
        "started_at": "2026-10-16T14:20:00+08:00",
        "finished_at": "2026-10-16T14:30:00+08:00",
        "status": status,
        "exit_code": None,
        "process_exit_code": None,
        "summary": "人工恢复，已回读核对。",
    }
    receipt = {
        **execution,
        "readback_verified": True,
        "result_text": "保留原失败记录，实际表格已核对。",
        "issues": ["恒生科技日期未知"] if status == "partial" else [],
        "data_items": [
            {
                "name": name,
                "date": None if status == "partial" and name == "恒生科技" else "2026-10-15",
                "complete_date": (
                    None if status == "partial" and name == "恒生科技" else "2026-10-15"
                ),
                "status": "unknown" if status == "partial" and name == "恒生科技" else "available",
                "note": "保留真实来源日期。",
            }
            for name in sorted(EXPECTED_ERP_ITEMS)
        ],
    }
    base = service.root / "data/scheduler/erp-sheet"
    write_json(base / "last-execution.json", execution)
    write_json(base / "results/2026-10-16.json", receipt)
    return base, execution, receipt


@pytest.mark.parametrize("status", ["completed", "partial"])
def test_manual_erp_recovery_uses_verified_business_result_without_process_exit_code(
    service, status
):
    base, execution, _ = manual_erp_recovery(service, status=status)
    original = {"run_id": execution["recovery_of_run_id"], "status": "failed"}
    original_path = base / "runs/2026-10-16/state.json"
    write_json(original_path, original)
    item = task(service, "erp")
    assert item["last_run"]["status"] == status
    assert item["last_run"]["started_at"] == execution["started_at"]
    assert item["last_run"]["finished_at"] == execution["finished_at"]
    assert item["last_run"]["exit_code"] is None
    assert len(item["data_items"]) == 10
    assert json.loads(original_path.read_text()) == original


@pytest.mark.parametrize(
    "damage",
    [
        {"mode": "other"},
        {"execution_origin": None},
        {"execution_origin": "scheduled"},
        {"recovery_of_run_id": None},
        {"recovery_of_run_id": "not-a-uuid"},
        {"exit_code": 0},
        {"process_exit_code": 0},
        {"finished_at": "2026-10-16T17:00:00+08:00"},
        {"status": "running", "finished_at": None},
        "same_run",
    ],
)
def test_manual_erp_recovery_rejects_invalid_execution_identity_or_time(service, damage):
    base, execution, _ = manual_erp_recovery(service)
    execution.update(
        {"recovery_of_run_id": execution["run_id"]} if damage == "same_run" else damage
    )
    write_json(base / "last-execution.json", execution)
    item = task(service, "erp")
    assert item["last_run"]["status"] == "unknown"
    assert not item["data_items"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        {"run_id": str(uuid4())},
        {"date": "2026-10-15"},
        {"mode": "codex_exec"},
        {"execution_origin": None},
        {"recovery_of_run_id": str(uuid4())},
        {"status": "completed"},
        {"readback_verified": False},
        {"started_at": "2026-10-16T14:19:00+08:00"},
        {"finished_at": "2026-10-16T14:31:00+08:00"},
    ],
)
def test_manual_erp_recovery_requires_matching_validated_readback(service, damage):
    base, _, receipt = manual_erp_recovery(service)
    path = base / "results/2026-10-16.json"
    if damage == "missing":
        path.unlink()
    else:
        receipt.update(damage)
        write_json(path, receipt)
    item = task(service, "erp")
    assert item["last_run"]["status"] == "unknown"
    assert not item["data_items"]


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unverified_date"])
def test_manual_erp_completed_requires_all_ten_verified_targets(service, damage):
    base, _, receipt = manual_erp_recovery(service, status="completed")
    if damage == "missing":
        receipt["data_items"].pop()
    elif damage == "duplicate":
        receipt["data_items"][-1] = receipt["data_items"][0]
    else:
        receipt["data_items"][0]["complete_date"] = None
    write_json(base / "results/2026-10-16.json", receipt)
    item = task(service, "erp")
    assert item["last_run"]["status"] == "unknown"
    assert not item["data_items"]


@pytest.mark.parametrize(
    "key,arguments",
    [
        ("index_us", ["--market", "CN", "HK"]),
        ("portfolio_us", ["run", "cn_hk_funds"]),
        ("policy_daily", ["preview"]),
    ],
)
def test_plist_wrong_market_or_portfolio_scope_is_not_attributed(service, key, arguments):
    definition = next(t for t in TASKS if t.id == key)
    path = service.home / f"Library/LaunchAgents/{definition.label}.plist"
    payload = plistlib.loads(path.read_bytes())
    payload["ProgramArguments"] = payload["ProgramArguments"][:2] + arguments
    path.write_bytes(plistlib.dumps(payload))
    assert task(service, key)["scheduler"]["state"] == "unknown"


@pytest.mark.parametrize(
    "updates",
    [
        {"markets": ["CN", "HK"]},
        {"finished_at": None},
        {"exit_code": None},
        {"finished_at": "2026-10-16T08:00:00+08:00"},
        {"status": "finished", "exit_code": 0, "run_ids": []},
    ],
)
def test_index_receipts_with_wrong_scope_or_missing_outcome_are_unknown(service, updates):
    record = {
        "markets": ["US"],
        "status": "partial",
        "started_at": "2026-10-16T09:00:00+08:00",
        "finished_at": "2026-10-16T09:10:00+08:00",
        "exit_code": 2,
        "run_ids": [],
    }
    record.update(updates)
    write_json(service.root / "data/scheduler/index-runs/us.latest.json", record)
    assert task(service, "index_us")["last_run"]["status"] == "unknown"


@pytest.mark.parametrize(
    "finished", [None, "2026-10-16T08:00:00+08:00", "2026-10-17T09:00:00+08:00"]
)
def test_portfolio_finished_requires_coherent_finish_time(service, finished):
    base = portfolio(service, finished_at=finished)
    (base / "us.result.md").write_text("本次结果")
    assert task(service, "portfolio_us")["last_run"]["status"] == "unknown"


@pytest.mark.parametrize("damaged", ["erp", "portfolio_us", "index_us"])
def test_valid_json_with_invalid_field_types_only_marks_affected_task_unknown(service, damaged):
    queue = queued(service)
    completed(service, queue)
    base = portfolio(service)
    (base / "us.result.md").write_text("本次已返回报价结果")
    if damaged == "erp":
        completed(service, queue, status=["completed"])
    elif damaged == "portfolio_us":
        portfolio(service, status={"unexpected": "object"})
    else:
        log = service.root / "logs/daily.us.stdout.log"
        log.parent.mkdir()
        log.write_text(json.dumps([{"mode": ["direct_index"], "index": "us"}]))
    app = create_app(repository=service.repository, specs=service.specs, scheduled_tasks=service)
    with TestClient(app) as client:
        response = client.get("/api/scheduled-tasks")
    assert response.status_code == 200
    tasks = {item["id"]: item for item in response.json()["tasks"]}
    assert len(tasks) == 6
    assert tasks[damaged]["last_run"]["status"] == "unknown"
    assert tasks[damaged]["warnings"]
    for key, expected in {
        "erp": "completed",
        "portfolio_us": "finished",
        "index_us": "never_run",
        "index_asia": "never_run",
        "portfolio_asia": "never_run",
    }.items():
        if key != damaged:
            assert tasks[key]["last_run"]["status"] == expected


def enable_snapshot(service, payload=None):
    payload = service.scheduler_metadata() if payload is None else payload
    path = service.root / "data/scheduler/host-status/status.json"
    write_scheduler_snapshot(path, payload)
    service.snapshot_file = path

    def forbidden(_):
        raise AssertionError("Container must never invoke launchctl")

    service.launchctl = forbidden
    return path


def test_metadata_export_does_not_read_database_receipts_or_environment(service):
    service.repository = None
    queue = queued(service)
    completed(service, queue, result_text="private-result-not-for-snapshot")
    metadata = service.scheduler_metadata()
    assert len(metadata["tasks"]) == 6
    text = json.dumps(metadata)
    assert "must-not-expose" not in text
    assert "private-result-not-for-snapshot" not in text
    assert "last_run" not in text
    assert "data_items" not in text
    assert "postgres" not in text


def test_container_uses_fresh_host_snapshot_and_reads_live_receipts_and_database(service):
    enable_snapshot(service)
    queue = queued(service)
    completed(service, queue)
    service.repository.summaries = {
        "us": {
            "latest": {"trade_date": "2026-10-16", "implied_eps": 1},
            "complete_date": "2026-10-16",
        }
    }
    data = service.snapshot()
    items = {item["id"]: item for item in data["tasks"]}
    assert items["index_asia"]["next_run_at"] == "2026-10-16T17:00:00+08:00"
    assert items["index_us"]["scheduler"]["state"] == "enabled"
    assert items["index_us"]["scheduler"]["checked_at"] == NOW.isoformat()
    assert items["index_us"]["scheduler"]["source"] == "host_snapshot"
    assert items["index_us"]["data_items"][0]["date"] == "2026-10-16"
    assert items["erp"]["last_run"]["status"] == "completed"
    completed(service, queue, status="failed", exit_code=1)
    assert task(service, "erp")["last_run"]["status"] == "failed"


@pytest.mark.parametrize("age", [181, -1])
def test_stale_or_future_snapshot_is_unknown_but_preserves_execution_evidence(service, age):
    metadata = service.scheduler_metadata()
    metadata["checked_at"] = (NOW - timedelta(seconds=age)).isoformat()
    enable_snapshot(service, metadata)
    queue = queued(service)
    completed(service, queue)
    items = {item["id"]: item for item in service.snapshot()["tasks"]}
    assert all(item["scheduler"]["state"] == "unknown" for item in items.values())
    assert all(item["next_run_at"] is None for item in items.values())
    assert items["erp"]["last_run"]["status"] == "completed"
    assert any(("过期" if age > 0 else "未来") in w for w in items["erp"]["warnings"])


@pytest.mark.parametrize("damage", ["schema", "identity", "duplicate", "timestamp", "missing"])
def test_missing_or_invalid_snapshot_never_falls_back_to_local_scheduler(service, damage):
    metadata = service.scheduler_metadata()
    if damage == "schema":
        metadata["schema"] = "wrong-schema"
    elif damage == "identity":
        metadata["tasks"][0]["id"] = "unrelated-project"
    elif damage == "duplicate":
        metadata["tasks"][0] = metadata["tasks"][1]
    elif damage == "timestamp":
        metadata["checked_at"] = "2026-10-16T16:00:00"
    path = enable_snapshot(service, metadata)
    if damage == "missing":
        path.unlink()
    for item in service.snapshot()["tasks"]:
        assert item["scheduler"]["state"] == "unknown"
        assert item["next_run_at"] is None
        assert item["warnings"]


@pytest.mark.parametrize("damage", ["label", "runs", "state", "next_run"])
def test_bad_snapshot_task_does_not_hide_other_tasks(service, damage):
    metadata = service.scheduler_metadata()
    row = next(row for row in metadata["tasks"] if row["id"] == "index_us")
    if damage == "label":
        row["scheduler"]["label"] = "cn.index-valuation.daily.asia"
    elif damage == "runs":
        row["scheduler"]["runs"] = True
    elif damage == "state":
        row["scheduler"]["state"] = ["enabled"]
    else:
        row["next_run_at"] = "2027-01-01T09:00:00+08:00"
    enable_snapshot(service, metadata)
    items = {item["id"]: item for item in service.snapshot()["tasks"]}
    assert items["index_us"]["scheduler"]["state"] == "unknown"
    assert items["index_asia"]["scheduler"]["state"] == "enabled"


@pytest.mark.parametrize("damage", ["symlink", "oversize", "fifo"])
def test_snapshot_reads_are_bounded_and_reject_special_files(service, tmp_path, damage):
    path = enable_snapshot(service)
    path.unlink()
    if damage == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text('{"secret":"do-not-expose"}')
        path.symlink_to(outside)
    elif damage == "oversize":
        path.write_bytes(b" " * (MAX_SCHEDULER_SNAPSHOT + 1))
    else:
        os.mkfifo(path)
    data = service.snapshot()
    assert all(item["scheduler"]["state"] == "unknown" for item in data["tasks"])
    assert "do-not-expose" not in json.dumps(data)


def test_fresh_snapshot_rolls_next_occurrence_forward_after_scheduled_minute(service):
    service.now = lambda: NOW.replace(hour=16, minute=59, second=45)
    enable_snapshot(service)
    service.now = lambda: NOW.replace(hour=17, minute=0, second=30)
    item = task(service, "index_asia")
    assert item["scheduler"]["state"] == "enabled"
    assert item["next_run_at"] == "2026-10-17T17:00:00+08:00"


def test_linux_without_snapshot_never_invokes_launchctl(tmp_path, monkeypatch):
    from index_valuation import scheduled_tasks

    monkeypatch.setattr(scheduled_tasks.sys, "platform", "linux")
    service = ScheduledTasks(ReadOnlyRepository(), {}, root=tmp_path, now=lambda: NOW)
    service.launchctl = lambda _: pytest.fail("Linux must not invoke launchctl")
    assert all(item["scheduler"]["state"] == "unknown" for item in service.snapshot()["tasks"])


def test_snapshot_atomic_write_failure_preserves_previous_file(service, monkeypatch):
    from index_valuation import scheduled_tasks

    path = enable_snapshot(service)
    original = path.read_bytes()

    def fail(*_):
        raise OSError("simulated failed replacement")

    monkeypatch.setattr(scheduled_tasks.os, "replace", fail)
    with pytest.raises(OSError):
        write_scheduler_snapshot(path, {"different": True})
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".host-status-*"))


def test_running_snapshot_does_not_replace_a_newer_completed_receipt(service):
    service.now = lambda: NOW.replace(hour=9)
    service.launchctl = lambda _: SimpleNamespace(
        returncode=0, stdout="state = running\n", stderr=""
    )
    enable_snapshot(service)
    base = portfolio(
        service,
        started_at="2026-10-16T08:59:55+08:00",
        finished_at="2026-10-16T09:00:10+08:00",
    )
    (base / "us.result.md").write_text("本次报价已更新")
    service.now = lambda: NOW.replace(hour=9, second=20)
    item = task(service, "portfolio_us")
    assert item["scheduler"]["state"] == "running"
    assert item["last_run"]["status"] == "finished"
    assert item["last_run"]["finished_at"] == "2026-10-16T09:00:10+08:00"
    assert any("采样早于" in warning for warning in item["warnings"])


def test_enabled_snapshot_does_not_mark_a_newer_running_receipt_unknown(service):
    service.now = lambda: NOW.replace(hour=9)
    enable_snapshot(service)
    portfolio(
        service,
        status="attempt_started",
        started_at="2026-10-16T09:00:10+08:00",
        finished_at=None,
        exit_code=None,
    )
    service.now = lambda: NOW.replace(hour=9, second=20)
    item = task(service, "portfolio_us")
    assert item["scheduler"]["state"] == "enabled"
    assert item["last_run"]["status"] == "running"
    assert item["last_run"]["started_at"] == "2026-10-16T09:00:10+08:00"
    assert any("采样早于" in warning for warning in item["warnings"])
