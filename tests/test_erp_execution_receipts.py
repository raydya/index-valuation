import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from index_valuation import erp_results
from index_valuation.scheduled_tasks import ZONE, ScheduledTasks

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "erp_execution_recorder", ROOT / "scripts/record_erp_result.py"
)
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
NOW = datetime(2026, 10, 20, 19, tzinfo=ZONE)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(erp_results, "datetime", Clock)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def execution(root, **updates):
    state = {
        "schema": "erp_execution_v1",
        "task": "erp",
        "mode": "codex_exec",
        "date": "2026-10-20",
        "run_id": str(uuid4()),
        "started_at": "2026-10-20T18:40:00+08:00",
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "summary": "正在执行 ERP 更新。",
    }
    state.update(updates)
    write(root / "data/scheduler/erp-sheet/last-execution.json", state)
    return state


def result(state, **updates):
    payload = {
        "date": state["date"],
        "run_id": state["run_id"],
        "started_at": "2026-10-20T18:41:00+08:00",
        "finished_at": "2026-10-20T18:50:00+08:00",
        "status": "partial",
        "readback_verified": True,
        "summary": "已回读，恒生科技日期未核实。",
        "issues": ["恒生科技日期未知"],
        "data_items": [
            {"name": "恒生科技", "date": None, "complete_date": None, "status": "unknown"}
        ],
        "result_text": "已核对必要输入和计算公式，保留未核实值。",
    }
    payload.update(updates)
    return payload


def complete_result(state):
    return result(
        state,
        status="completed",
        summary="十项输入均已核对并回读。",
        issues=[],
        data_items=[
            {
                "name": name,
                "date": "2026-10-19",
                "complete_date": "2026-10-19",
                "status": "available",
            }
            for name in sorted(erp_results.EXPECTED_ERP_ITEMS)
        ],
    )


def view(root):
    warnings = []
    service = ScheduledTasks(None, {}, root=root, now=lambda: NOW)
    run, items = service._erp_run(warnings)
    return run, items, warnings


def stored_result(root, payload):
    write(
        root / f"data/scheduler/erp-sheet/results/{payload['date']}.json",
        {"task": "erp", **payload},
    )


def test_direct_result_binds_to_running_execution_and_preserves_real_dates(tmp_path):
    state = execution(tmp_path)
    payload = result(state)
    saved = json.loads(recorder.save_result(tmp_path, payload).read_text())
    assert saved["run_id"] == state["run_id"]
    assert "queued_at" not in saved and "thread" not in saved
    assert saved["data_items"][0]["date"] is None
    assert saved["readback_verified"] is True


def test_direct_completed_result_requires_and_accepts_all_ten_named_items(tmp_path):
    state = execution(tmp_path)
    payload = complete_result(state)
    saved = json.loads(recorder.save_result(tmp_path, payload).read_text())
    assert saved["status"] == "completed"
    assert len(saved["data_items"]) == 10
    execution(
        tmp_path,
        run_id=state["run_id"],
        status="completed",
        finished_at="2026-10-20T18:51:00+08:00",
        exit_code=0,
    )
    run, items, _ = view(tmp_path)
    assert run["status"] == "completed" and len(items) == 10


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unexpected"])
def test_direct_completed_result_and_dashboard_reject_incomplete_target_coverage(tmp_path, damage):
    state = execution(tmp_path)
    payload = complete_result(state)
    if damage == "missing":
        payload["data_items"].pop()
    elif damage == "duplicate":
        payload["data_items"][-1] = payload["data_items"][0]
    else:
        payload["data_items"][-1]["name"] = "未授权目标"
    with pytest.raises(ValueError, match="全部十项"):
        recorder.save_result(tmp_path, payload)
    stored_result(tmp_path, payload)
    execution(
        tmp_path,
        run_id=state["run_id"],
        status="completed",
        finished_at="2026-10-20T18:51:00+08:00",
        exit_code=0,
    )
    run, items, _ = view(tmp_path)
    assert run["status"] == "unknown" and not items


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": str(uuid4())},
        {"run_id": "not-a-uuid"},
        {"run_id": None},
        {"date": "2026-10-19"},
        {"started_at": "2026-10-20T18:39:59+08:00"},
        {"started_at": "2026-10-20T18:41:00"},
        {"finished_at": "2026-10-20T18:40:01+08:00"},
        {"finished_at": "2026-10-20T19:00:01+08:00"},
        {"readback_verified": False},
        {"status": "completed"},
    ],
)
def test_direct_result_rejects_wrong_run_or_unverified_claims(tmp_path, changes):
    state = execution(tmp_path)
    with pytest.raises(ValueError):
        recorder.save_result(tmp_path, result(state, **changes))
    assert not (tmp_path / "data/scheduler/erp-sheet/results").exists()


@pytest.mark.parametrize("status", ["completed", "failed", "timed_out", "interrupted"])
def test_closed_execution_rejects_late_result(tmp_path, status):
    state = execution(tmp_path, status=status, finished_at="2026-10-20T18:51:00+08:00", exit_code=1)
    with pytest.raises(ValueError):
        recorder.save_result(tmp_path, result(state))


def test_new_execution_rejects_legacy_queue_result_without_overwriting_new_result(tmp_path):
    state = execution(tmp_path)
    new = recorder.save_result(tmp_path, result(state))
    original = new.read_bytes()
    legacy = {
        "date": "2026-10-20",
        "thread": str(uuid4()),
        "queued_at": "2026-10-20T08:13:00+08:00",
    }
    write(tmp_path / "data/scheduler/erp-sheet/last-queued.json", legacy)
    old_result = result(state)
    old_result.pop("run_id")
    with pytest.raises(ValueError):
        recorder.save_result(tmp_path, {**old_result, **legacy})
    assert new.read_bytes() == original


def test_dashboard_new_execution_supersedes_old_queue_and_waits_for_runner(tmp_path):
    state = execution(tmp_path)
    write(
        tmp_path / "data/scheduler/erp-sheet/last-queued.json",
        {"date": "2026-10-20", "thread": "old", "queued_at": "2026-10-20T08:13:00+08:00"},
    )
    stored_result(tmp_path, result(state))
    run, items, _ = view(tmp_path)
    assert run["status"] == "running" and run["finished_at"] is None
    assert run["started_at"] == state["started_at"]
    assert run["queued_at"] is None
    assert items[0]["date"] is None


def test_dashboard_direct_partial_reads_matched_business_receipt(tmp_path):
    state = execution(
        tmp_path, status="partial", finished_at="2026-10-20T18:51:00+08:00", exit_code=2
    )
    stored_result(tmp_path, result(state))
    run, items, warnings = view(tmp_path)
    assert run["status"] == "partial"
    assert run["exit_code"] == 2 and run["queued_at"] is None
    assert run["result_text"] == result(state)["result_text"]
    assert items[0]["date"] is None
    assert not warnings


@pytest.mark.parametrize("status", ["failed", "timed_out", "interrupted"])
def test_runner_failure_is_not_hidden_by_an_earlier_business_receipt(tmp_path, status):
    state = execution(
        tmp_path,
        status=status,
        finished_at="2026-10-20T18:51:00+08:00",
        exit_code=124,
        summary="执行进程未正常结束，需要核对。",
    )
    stored_result(tmp_path, result(state))
    run, items, _ = view(tmp_path)
    assert run["status"] == ("failed" if status == "interrupted" else status)
    assert "执行进程未正常结束" in " ".join(run["issues"])
    assert run["result_text"] == result(state)["result_text"]
    assert items


@pytest.mark.parametrize("damage", ["missing", "wrong_run", "unverified", "bad_times"])
def test_dashboard_does_not_claim_completion_without_valid_current_business_result(
    tmp_path, damage
):
    state = execution(
        tmp_path, status="completed", finished_at="2026-10-20T18:51:00+08:00", exit_code=0
    )
    if damage != "missing":
        changes = {
            "wrong_run": {"run_id": str(uuid4())},
            "unverified": {"readback_verified": False},
            "bad_times": {"started_at": "2026-10-20T18:39:00+08:00"},
        }[damage]
        stored_result(tmp_path, result(state, **changes))
    run, items, _ = view(tmp_path)
    assert run["status"] == "unknown"
    assert not items


def test_corrupt_latest_execution_does_not_fall_back_to_old_queue(tmp_path):
    path = tmp_path / "data/scheduler/erp-sheet/last-execution.json"
    path.parent.mkdir(parents=True)
    path.write_text("invalid")
    write(
        path.with_name("last-queued.json"),
        {"date": "2026-10-20", "thread": "old", "queued_at": "2026-10-20T08:13:00+08:00"},
    )
    run, _, warnings = view(tmp_path)
    assert run["status"] == "unknown" and run["queued_at"] is None
    assert warnings


def test_stopped_scheduler_does_not_leave_crashed_execution_running_forever(tmp_path):
    execution(tmp_path)
    service = ScheduledTasks(
        None,
        {},
        root=tmp_path,
        home=tmp_path / "home",
        now=lambda: NOW,
        launchctl=lambda _: SimpleNamespace(
            returncode=0, stdout="state = not running\n", stderr=""
        ),
    )
    task = next(task for task in service.snapshot()["tasks"] if task["id"] == "erp")
    assert task["last_run"]["status"] == "unknown"
    assert "未发现运行进程" in task["last_run"]["summary"]


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "invalid"},
        {"date": "2026-10-19"},
        {"schema": "unrecognized"},
        {"status": "queued"},
        {"started_at": "2026-10-20T19:00:01+08:00"},
        {"finished_at": "2026-10-20T18:39:00+08:00"},
    ],
)
def test_invalid_direct_execution_never_appears_completed(tmp_path, changes):
    state = execution(
        tmp_path,
        **{
            "status": "completed",
            "finished_at": "2026-10-20T18:51:00+08:00",
            "exit_code": 0,
            **changes,
        },
    )
    stored_result(tmp_path, result(state))
    run, items, _ = view(tmp_path)
    assert run["status"] == "unknown"
    assert not items
