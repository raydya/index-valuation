import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from index_valuation.domain import IndexSpec
from index_valuation.run_journal import ResultJournal
from index_valuation.scheduled_tasks import ScheduledTasks
from index_valuation.task_digest import (
    SCHEMA,
    ZONE,
    build_task_digest,
    build_task_notification,
    collect_summary,
    read_index_batches,
    send_task_summary,
)
from index_valuation.telegram_notifications import (
    TelegramError,
    TelegramSettings,
    deliver,
    delivery_lock,
)

NOW = datetime(2026, 10, 20, 18, 5, tzinfo=ZONE)
SPEC = IndexSpec("us", "测试美股", "US", "US.INDEX")


def settings(**kwargs):
    return TelegramSettings(
        _env_file=None,
        telegram_enabled=True,
        telegram_bot_token="123456:" + "test_token_" * 4,
        telegram_chat_id=123,
        **kwargs,
    )


def task(key, *, day="2026-10-20", status="finished", **updates):
    run = {
        "status": status,
        "started_at": f"{day}T09:03:00+08:00",
        "finished_at": f"{day}T09:13:00+08:00",
        "queued_at": None,
        "summary": "",
        "result_text": "",
        "issues": [],
    }
    run.update(updates)
    return {
        "id": key,
        "name": key,
        "schedule_text": "每天 09:00（北京时间，含周末）",
        "last_run": run,
        "warnings": [],
    }


def snapshot(*tasks, now=NOW, warnings=None):
    return {
        "checked_at": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "tasks": list(tasks),
        "warnings": warnings or [],
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False))


def test_old_receipts_are_not_presented_as_todays_completed_tasks():
    text = build_task_digest(
        snapshot(
            task("index_us", day="2026-10-19"),
            task(
                "erp",
                day="2026-10-19",
                status="queued",
                started_at=None,
                finished_at=None,
                queued_at="2026-10-19T08:13:00+08:00",
            ),
        ),
        {"index_us": {"lines": ["must not appear as today's data"]}},
    )
    assert "index_us｜—｜今日无执行回执" in text
    assert "erp｜—｜今日无执行回执" in text
    assert "10-19" not in text
    assert "must not appear" not in text


def test_same_day_queued_erp_is_not_sheet_completion():
    row = task(
        "erp",
        status="queued",
        queued_at="2026-10-20T08:13:00+08:00",
        started_at=None,
        finished_at=None,
    )
    row["warnings"] = ["提交后已等待 10 小时，仍未取得匹配的完成回执"]
    text = build_task_digest(snapshot(row))
    assert "erp｜08:13 提交｜排队中" in text
    assert "等待 10 小时" not in text
    assert "回读" not in text


def test_completed_tasks_only_show_name_time_and_result():
    row = task(
        "policy_daily",
        status="completed",
        started_at="2026-10-20T16:00:00+08:00",
        finished_at="2026-10-20T16:12:00+08:00",
        summary="已检查主要官方来源，今日没有发现与924相当的综合政策组合。",
        result_text="局部支持：降低企业融资成本。分析判断：偏正面，传导仍需观察。",
        notification_status="sent",
    )
    row["schedule_text"] = "每天 16:00（北京时间，含周末）"
    text = build_task_digest(snapshot(row))
    assert "policy_daily｜16:00–16:12｜完成" in text
    assert "计划" not in text
    assert "今日没有发现与924相当" not in text
    assert "偏正面" not in text
    assert "Telegram" not in text
    assert "回读" not in text
    erp_text = build_task_digest(snapshot(task("erp", status="completed")))
    assert "erp｜09:03–09:13｜完成" in erp_text


def test_old_policy_digest_does_not_repeat_prior_conclusion_or_delivery():
    text = build_task_digest(
        snapshot(
            task(
                "policy_daily",
                day="2026-10-19",
                status="completed",
                summary="旧日政策判断",
                result_text="旧日分析",
                notification_status="sent",
            )
        )
    )
    assert "今日无执行回执" in text
    assert "旧日" not in text
    assert "政策日报 Telegram 已发送" not in text
    assert "回读" not in text


def test_policy_finished_after_midnight_keeps_original_research_date():
    row = task(
        "policy_daily",
        day="2026-10-19",
        status="completed",
        date="2026-10-19",
        started_at="2026-10-19T23:59:00+08:00",
        finished_at="2026-10-20T00:12:00+08:00",
        result_text="昨日政策结论",
    )
    text = build_task_digest(snapshot(row))
    assert "policy_daily｜—｜今日无执行回执" in text
    assert "昨日政策结论" not in text
    assert "10-19 23:59" not in text


def test_portfolio_process_success_is_not_upgraded_to_verified_business_success():
    row = task(
        "portfolio_us",
        result_text=(
            "## 仅完成部分报价\n\n"
            "| 证券 | 价格 |\n| --- | --- |\n| AAA | 20 |\n\n"
            "已更新 **5只**，QQQ读取失败，保留旧价格。回读存在公式错误。\n"
            ':codex-file-citation{path="https://example.com/private" purpose="output"}'
        ),
    )
    text = build_task_digest(snapshot(row))
    assert "portfolio_us｜09:03–09:13｜已结束，结果待确认" in text
    assert "QQQ" not in text and "公式错误" not in text
    assert "AAA" not in text and "codex-file-citation" not in text
    assert "private" not in text


def test_cross_day_finish_shows_original_start_and_utc_dates_use_shanghai():
    row = task(
        "portfolio_us",
        started_at="2026-10-19T23:59:00+08:00",
        finished_at="2026-10-19T16:05:00+00:00",
        result_text="结果尚有缺口",
    )
    text = build_task_digest(snapshot(row))
    assert "portfolio_us｜10-19 23:59–00:05｜已结束，结果待确认" in text
    assert "今日未见执行回执\n最近记录" not in text


def test_running_without_new_receipt_never_reuses_previous_batch_data():
    row = task("index_us", status="running", started_at=None, finished_at=None)
    text = build_task_digest(snapshot(row), {"index_us": {"lines": ["old result must not appear"]}})
    assert "index_us｜—｜执行中（时间待确认）" in text
    assert "old result" not in text


def test_read_batch_uses_matching_journal_and_reports_real_band_gap(tmp_path):
    row = task("index_us", status="partial")
    base = tmp_path / "data/scheduler/index-runs"
    batch = str(uuid4())
    write_json(
        base / "us.latest.json",
        {
            **row["last_run"],
            "batch_id": batch,
            "markets": ["US"],
            "notification": {"status": "sent"},
        },
    )
    with ResultJournal(base / f"{batch}.results.jsonl", batch) as journal:
        journal.append(
            {
                "index": SPEC.key,
                "status": "partial",
                "latest": {"trade_date": "2026-10-19", "implied_eps": None},
                "latest_complete_date": "2026-10-16",
                "collection": {
                    "last_session": {"date": "2026-10-19"},
                    "sources": {
                        "nasdaq_price": {"status": "ok", "missing_sessions": 1},
                        "futu_price": {"status": "unsupported", "missing_sessions": 100},
                        "nasdaq_pe": {"status": "ok", "missing_sessions": 100},
                    },
                },
            }
        )
    data = snapshot(row)
    batches = read_index_batches(tmp_path, {SPEC.key: SPEC}, data)
    text = build_task_digest(data, batches)
    assert "index_us｜09:03–09:13｜部分完成" in text
    assert "Telegram" not in text and "band" not in text and "Nasdaq" not in text
    evidence = "\n".join(batches["index_us"]["lines"])
    assert "数据截至 2026-10-19，band 至 2026-10-16；部分完成" in evidence
    assert "Nasdaq 官方价格缺 1 个交易日" in evidence
    assert "100" not in evidence
    assert batches["index_us"]["batch_id"] == batch


def test_batch_changed_after_snapshot_is_not_misattributed(tmp_path):
    base = tmp_path / "data/scheduler/index-runs"
    write_json(
        base / "us.latest.json",
        {
            **task("index_us")["last_run"],
            "started_at": "2026-10-20T12:00:00+08:00",
            "batch_id": str(uuid4()),
            "markets": ["US"],
        },
    )
    result = read_index_batches(tmp_path, {SPEC.key: SPEC}, snapshot(task("index_us")))
    assert "明细缺失或不匹配" in result["index_us"]["lines"][0]
    assert "batch_id" not in result["index_us"]


def test_batch_paths_cannot_escape_trusted_directory(tmp_path):
    base = tmp_path / "data/scheduler/index-runs"
    base.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": "do not read"}')
    (base / "us.latest.json").symlink_to(outside)
    result = read_index_batches(tmp_path, {}, snapshot(task("index_us")))
    assert "明细缺失或不匹配" in result["index_us"]["lines"][0]


def test_snapshot_still_works_when_database_is_unavailable(tmp_path):
    class Unavailable:
        def scheduled_index_summaries(self, _specs):
            raise RuntimeError("database unavailable")

    service = ScheduledTasks(
        Unavailable(),
        {},
        root=tmp_path,
        home=tmp_path / "home",
        now=lambda: NOW,
        launchctl=lambda _: SimpleNamespace(
            returncode=113, stdout="", stderr="Could not find service"
        ),
    )
    write_json(
        tmp_path / "data/scheduler/erp-sheet/last-queued.json",
        {
            "date": "2026-10-20",
            "thread": "thread",
            "queued_at": "2026-10-20T08:13:00+08:00",
        },
    )
    result = collect_summary(tmp_path, {}, now=NOW, service=service)
    assert "数据库暂不可读" not in result["text"]
    assert any("数据库暂不可读" in warning for warning in result["snapshot"]["warnings"])
    assert "08:13 提交｜排队中" in result["text"]
    assert len(result["snapshot"]["tasks"]) == 6
    assert not (tmp_path / "data/scheduler/task-summaries").exists()


def test_native_table_has_only_three_columns_and_six_task_rows():
    row = task("index_us", status="partial", summary="omit summary", issues=["omit issue"])
    row["warnings"] = ["omit warning"]
    message = build_task_notification(snapshot(row, warnings=["omit global warning"]))
    blocks = message["rich_message"]["blocks"]
    assert blocks[0] == {"type": "heading", "size": 3, "text": "每日任务汇总：2026-10-20"}
    assert len(blocks) == 2 and blocks[1]["type"] == "table"
    cells = blocks[1]["cells"]
    assert len(cells) == 7 and all(len(row) == 3 for row in cells)
    assert [cell["text"] for cell in cells[0]] == ["任务名称", "执行时间", "执行结果"]
    assert [cell["text"] for cell in cells[1]] == ["index_us", "09:03–09:13", "部分完成"]
    assert len(message["text"].splitlines()) == 8
    assert "omit" not in json.dumps(message)


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("failed", "失败"),
        ("timed_out", "超时"),
        ("interrupted", "中断"),
        ("unknown", "待确认"),
        ("finished", "流程完成"),
    ],
)
def test_short_result_labels_preserve_execution_states(status, label):
    assert f"index_us｜09:03–09:13｜{label}" in build_task_digest(
        snapshot(task("index_us", status=status))
    )


def test_running_task_has_real_start_without_inventing_end_time():
    text = build_task_digest(snapshot(task("index_us", status="running", finished_at=None)))
    assert "index_us｜09:03 起｜执行中" in text


@pytest.mark.parametrize(
    "updates",
    [
        {"finished_at": "2026-10-20T19:00:00+08:00"},
        {"finished_at": "2026-10-20T08:00:00+08:00"},
        {"finished_at": None},
        {"started_at": "bad"},
    ],
)
def test_inconsistent_completion_times_do_not_claim_success(updates):
    text = build_task_digest(snapshot(task("erp", status="completed", **updates)))
    assert "erp｜—｜时间异常，待确认" in text


class Client:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    def private_chat(self, chat_id):
        assert chat_id == 123

    def send(self, _chat, text):
        self.sent.append(text)
        if self.error:
            error, self.error = self.error, None
            raise error
        return len(self.sent)

    def send_rich(self, chat, rich_message):
        return self.send(chat, rich_message)

    def close(self):
        pass


def collector(root, specs, *, now):
    return {
        "schema": SCHEMA,
        "date": now.astimezone(ZONE).date().isoformat(),
        "checked_at": now.astimezone(ZONE).isoformat(),
        "text": "ERP无完成回执；指数部分完成",
        "snapshot": snapshot(now=now),
    }


def rich_collector(root, specs, *, now):
    data = snapshot(task("index_us", status="partial"), now=now)
    return {
        **collector(root, specs, now=now),
        "snapshot": data,
        **build_task_notification(data),
    }


def test_new_daily_send_freezes_table_and_duplicate_does_not_resend(tmp_path):
    api = Client()

    def delivery(settings, text, path, **kwargs):
        return deliver(settings, text, path, client_factory=lambda _: api, **kwargs)

    first = send_task_summary(
        tmp_path, {}, settings(), now=NOW, collector=rich_collector, delivery=delivery
    )
    second = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=NOW + timedelta(hours=1),
        collector=lambda *_args, **_kwargs: pytest.fail("must use frozen summary"),
        delivery=delivery,
    )
    frozen = json.loads((tmp_path / "data/scheduler/task-summaries/2026-10-20.json").read_text())
    assert first == second and first["status"] == "sent"
    assert api.sent == [frozen["rich_message"]]
    assert frozen["text"] == build_task_digest(frozen["snapshot"])


@pytest.mark.parametrize("uncertain,expected_attempts", [(False, 2), (True, 1)])
def test_rich_retry_preserves_frozen_table_and_does_not_repeat_uncertain_send(
    tmp_path, uncertain, expected_attempts
):
    api = Client(TelegramError("failed", uncertain=uncertain))

    def delivery(settings, text, path, **kwargs):
        return deliver(settings, text, path, client_factory=lambda _: api, **kwargs)

    first = send_task_summary(
        tmp_path, {}, settings(), now=NOW, collector=rich_collector, delivery=delivery
    )
    assert first["status"] == ("delivery_unknown" if uncertain else "failed")
    second = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=NOW + timedelta(days=1),
        retry_date=date(2026, 10, 20),
        collector=lambda *_args, **_kwargs: pytest.fail("retry cannot create new summary"),
        delivery=delivery,
    )
    assert len(api.sent) == expected_attempts
    assert all(message == api.sent[0] for message in api.sent)
    assert second["status"] == ("delivery_unknown" if uncertain else "sent")


def test_duplicate_daily_send_keeps_frozen_evidence_and_never_sends_again(tmp_path):
    api = Client()

    def delivery(settings, text, path):
        return deliver(settings, text, path, client_factory=lambda _: api)

    first = send_task_summary(
        tmp_path, {}, settings(), now=NOW, collector=collector, delivery=delivery
    )
    second = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=NOW + timedelta(hours=1),
        collector=lambda *_args, **_kwargs: pytest.fail("must use frozen summary"),
        delivery=delivery,
    )
    assert first == second
    assert len(api.sent) == 1
    assert (tmp_path / "data/scheduler/task-summaries/2026-10-20.json").exists()
    receipt = tmp_path / "data/scheduler/notifications/task-summary-2026-10-20.json"
    assert json.loads(receipt.read_text())["message_ids"] == [1]


@pytest.mark.parametrize("uncertain,expected_attempts", [(False, 2), (True, 1)])
def test_retry_reuses_frozen_text_and_never_repeats_uncertain_send(
    tmp_path, uncertain, expected_attempts
):
    api = Client(TelegramError("failed", uncertain=uncertain))

    def delivery(settings, text, path):
        return deliver(settings, text, path, client_factory=lambda _: api)

    first = send_task_summary(
        tmp_path, {}, settings(), now=NOW, collector=collector, delivery=delivery
    )
    assert first["status"] == ("delivery_unknown" if uncertain else "failed")
    second = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=NOW + timedelta(days=1),
        retry_date=date(2026, 10, 20),
        collector=lambda *_args, **_kwargs: pytest.fail("retry cannot create new summary"),
        delivery=delivery,
    )
    assert len(api.sent) == expected_attempts
    assert len(set(api.sent)) == 1
    assert second["status"] == ("delivery_unknown" if uncertain else "sent")


@pytest.mark.parametrize("hour", [0, 8, 17])
def test_send_before_18_does_not_freeze_or_send_even_after_cross_day_wakeup(tmp_path, hour):
    result = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=NOW.replace(hour=hour),
        collector=lambda *_args, **_kwargs: pytest.fail("not due"),
        delivery=lambda *_args: pytest.fail("not due"),
    )
    assert result == {"status": "not_due", "date": "2026-10-20"}
    assert not (tmp_path / "data").exists()


def test_exact_18_utc_conversion_is_due_and_failed_delivery_can_retry_in_morning(tmp_path):
    utc_time = datetime.fromisoformat("2026-10-20T10:00:00+00:00")
    first = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=utc_time,
        collector=collector,
        delivery=lambda *_args: {"status": "failed"},
    )
    assert first["date"] == "2026-10-20" and first["status"] == "failed"
    second = send_task_summary(
        tmp_path,
        {},
        settings(),
        now=NOW.replace(day=21, hour=9),
        retry_date=date(2026, 10, 20),
        collector=lambda *_args, **_kwargs: pytest.fail("must use original text"),
        delivery=lambda *_args: {"status": "sent"},
    )
    assert second["status"] == "sent"


def test_concurrent_freeze_is_rejected_without_collecting_or_sending(tmp_path):
    path = tmp_path / "data/scheduler/task-summaries/2026-10-20.json"
    with delivery_lock(path), pytest.raises(TelegramError, match="delivery_in_progress"):
        send_task_summary(
            tmp_path,
            {},
            settings(),
            now=NOW,
            collector=lambda *_args, **_kwargs: pytest.fail("already held"),
        )


def test_retry_requires_existing_evidence_and_missing_evidence_never_replaces_delivery(tmp_path):
    with pytest.raises(TelegramError, match="summary_not_found"):
        send_task_summary(tmp_path, {}, settings(), now=NOW, retry_date=date(2026, 10, 19))
    write_json(
        tmp_path / "data/scheduler/notifications/task-summary-2026-10-20.json",
        {"status": "sent"},
    )
    with pytest.raises(TelegramError, match="summary_missing_for_delivery"):
        send_task_summary(tmp_path, {}, settings(), now=NOW)


def test_cli_preview_does_not_read_telegram_config_or_freeze(tmp_path, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/notify_task_summary.py"
    spec = importlib.util.spec_from_file_location("notify_task_summary_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "load_indices", lambda _: {})
    monkeypatch.setattr(module, "collect_summary", lambda *_: {"text": "只读预览"})
    monkeypatch.setattr(module, "TelegramSettings", lambda **_: pytest.fail("not needed"))
    assert module.main(["preview"]) == 0
    assert capsys.readouterr().out == "只读预览\n"
    assert list(tmp_path.iterdir()) == []
