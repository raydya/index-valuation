"""Daily task conclusions from dated execution evidence, with durable private delivery."""

import json
import re
from datetime import date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from index_valuation.domain import DataError
from index_valuation.notification_digest import _source_issues
from index_valuation.run_journal import read_reports
from index_valuation.scheduled_tasks import TASKS, ScheduledTasks
from index_valuation.telegram_notifications import (
    TelegramError,
    deliver,
    delivery_lock,
    save_json,
)

ZONE = ZoneInfo("Asia/Shanghai")
SCHEMA = "daily_task_summary_v1"
ORDER = ("index_us", "index_asia", "portfolio_us", "portfolio_asia", "erp", "policy_daily")
LABELS = {
    "finished": "流程完成",
    "completed": "完成",
    "partial": "部分完成",
    "failed": "失败",
    "timed_out": "超时",
    "interrupted": "中断",
    "running": "执行中",
    "queued": "排队中",
    "unknown": "待确认",
    "never_run": "无执行回执",
}


def _status_label(key, status):
    if key.startswith("portfolio_") and status == "finished":
        return "已结束，结果待确认"
    return LABELS.get(status, "待确认")


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _time(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if isinstance(parsed, datetime) and parsed.tzinfo is not None:
            return parsed.astimezone(ZONE)
    except ValueError:
        pass
    return None


def _day(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _plain(value, limit=600):
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    text = re.sub(r":codex-file-citation\{[^}]*\}", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return " ".join(text.split())[:limit]


def _times(run):
    return {key: _time(run.get(key)) for key in ("queued_at", "started_at", "finished_at")}


def _belongs_today(run, today, checked):
    # Policy reports are assigned to the search window's date, even if a late
    # execution happens to finish after midnight. Never repeat yesterday's news.
    if run.get("date") is not None and _day(run["date"]) != today:
        return False
    return any(
        value is not None and value.date() == today and value <= checked
        for value in _times(run).values()
    )


def _clock(value, today):
    if value is None:
        return "未知"
    return value.strftime("%H:%M" if value.date() == today else "%m-%d %H:%M")


def _execution(run, today):
    times = _times(run)
    start, finish, queued = (times[key] for key in ("started_at", "finished_at", "queued_at"))
    if start:
        interval = _clock(start, today)
        interval += "–" + _clock(finish, today) if finish else " 起"
        return interval
    if queued:
        return _clock(queued, today) + " 提交"
    return f"{_clock(finish, today)} 结束" if finish else "—"


def _read_object(path, base, maximum=128 * 1024):
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("回执路径越界")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("回执过大")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("回执不是对象")
    return payload


def _index_lines(specs, markets, reports):
    selected = {key: spec for key, spec in specs.items() if spec.market in markets}
    by_key = {
        report["index"]: report
        for report in reports
        if isinstance(report, dict) and report.get("index") in selected
    }
    lines, issues = [], {}
    for key, spec in selected.items():
        report = by_key.get(key, {})
        name = _plain(spec.name, 80)
        if not spec.collection_enabled:
            lines.append(f"{name}：按配置暂停采集")
            continue
        if not report:
            lines.append(f"{name}：无本轮结果，未采用旧数据代替")
            continue
        if report.get("status") == "blocked":
            lines.append(f"{name}：本轮阻塞，未完成采集")
            continue
        latest = _mapping(report.get("latest"))
        collection = _mapping(report.get("collection"))
        day = _day(latest.get("trade_date"))
        target = _day(_mapping(collection.get("last_session")).get("date"))
        complete = _day(report.get("latest_complete_date"))
        detail = f"数据截至 {day}" if day else "数据日期未知"
        if target and day != target:
            detail += f"（目标 {target}）"
        elif not target:
            detail += "（目标交易日未知）"
        if complete and complete == day and latest.get("implied_eps") is not None:
            detail += "，band 同日可用"
        else:
            detail += f"，band 至 {complete}" if complete else "，band 尚无完整日期"
        if report.get("status") in {"failed", "partial"}:
            detail += "；" + ("处理失败" if report["status"] == "failed" else "部分完成")
        if collection.get("status") == "failed":
            detail += "；本轮采集失败，所列日期为已有数据"
        lines.append(f"{name}：{detail}")
        source_issues = _source_issues(
            collection.get("sources"), primary_source=spec.valuation_source
        )
        returns = _mapping(report.get("total_return_collection"))
        source_issues.extend(
            "全收益：" + issue
            for issue in _source_issues(returns.get("sources"), total_return=True)
        )
        if returns.get("status") in {"partial", "failed", "blocked"}:
            source_issues.append("分红再投资数据存在缺口")
        for issue in dict.fromkeys(source_issues):
            issues.setdefault(issue, []).append(name)
    if issues:
        lines.append(
            "来源/数据缺口："
            + "；".join(f"{issue}（{'、'.join(names)}）" for issue, names in issues.items())
        )
    return lines


def read_index_batches(root, specs, snapshot):
    """Use only journals matching the same dated invocation shown in the task snapshot."""
    root = Path(root)
    base = root / "data/scheduler/index-runs"
    checked = _time(snapshot.get("checked_at"))
    if checked is None:
        raise ValueError("缺少汇总检查时间")
    batches = {}
    for task in snapshot.get("tasks", []):
        key = task.get("id")
        if key not in {"index_us", "index_asia"}:
            continue
        run = _mapping(task.get("last_run"))
        if not _belongs_today(run, checked.date(), checked):
            continue
        market = "us" if key == "index_us" else "asia"
        markets = ["US"] if market == "us" else ["CN", "HK"]
        try:
            receipt = _read_object(base / f"{market}.latest.json", base)
            if (
                receipt.get("markets") != markets
                or _time(receipt.get("started_at")) != _time(run.get("started_at"))
                or _time(run.get("started_at")) is None
                or _time(receipt.get("finished_at")) != _time(run.get("finished_at"))
            ):
                raise ValueError("回执已变化或不匹配")
            batch_id = str(UUID(receipt["batch_id"]))
            journal = base / f"{batch_id}.results.jsonl"
            if not journal.resolve().is_relative_to(base.resolve()):
                raise ValueError("批次路径越界")
            if journal.exists() and journal.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("批次结果过大")
            reports = read_reports(journal, batch_id)
            batches[key] = {
                "batch_id": batch_id,
                "notification_status": _plain(
                    _mapping(receipt.get("notification")).get("status"), 80
                ),
                "lines": _index_lines(specs, markets, reports),
            }
        except (OSError, ValueError, TypeError, KeyError, DataError):
            batches[key] = {"lines": ["本次批次明细缺失或不匹配，数据日期与缺口待核对"]}
    return batches


def _invalid_times(run, checked):
    times = _times(run)
    if any(run.get(key) is not None and value is None for key, value in times.items()):
        return True
    if any(value > checked for value in times.values() if value is not None):
        return True
    queued, start, finish = (times[key] for key in ("queued_at", "started_at", "finished_at"))
    if (start and finish and finish < start) or (queued and start and start < queued):
        return True
    return run.get("status") in {"completed", "finished"} and (start is None or finish is None)


def build_task_notification(snapshot, batches=None):
    """Render three fields per task; detailed evidence stays in the frozen summary."""
    checked = _time(snapshot.get("checked_at"))
    if checked is None:
        raise ValueError("缺少汇总检查时间")
    today = checked.date()
    rows = {row.get("id"): row for row in snapshot.get("tasks", []) if isinstance(row, dict)}
    task_names = {task.id: task.name for task in TASKS}
    values = []
    for key in ORDER:
        row = _mapping(rows.get(key))
        run = _mapping(row.get("last_run"))
        name = _plain(row.get("name"), 100) or task_names[key]
        status = run.get("status")
        if _invalid_times(run, checked):
            execution, result = "—", "时间异常，待确认"
        elif not _belongs_today(run, today, checked):
            execution = "—"
            if status == "running" and not any(_times(run).values()):
                result = "执行中（时间待确认）"
            elif status == "unknown" and not any(_times(run).values()):
                result = "待确认"
            else:
                result = "今日无执行回执"
        else:
            execution, result = _execution(run, today), _status_label(key, status)
        values.append([name, execution, result])
    title = f"每日任务汇总：{today}"
    headers = ["任务名称", "执行时间", "执行结果"]
    cells = [
        [
            {
                "text": value,
                "align": "center" if number == 0 or column == 1 else "left",
                "valign": "middle",
                **({"is_header": True} if number == 0 else {}),
            }
            for column, value in enumerate(row)
        ]
        for number, row in enumerate([headers, *values])
    ]
    return {
        "text": "\n".join([title, "｜".join(headers), *("｜".join(row) for row in values)]),
        "rich_message": {
            "blocks": [
                {"type": "heading", "size": 3, "text": title},
                {
                    "type": "table",
                    "cells": cells,
                    "is_bordered": True,
                    "is_striped": True,
                    "is_compact": True,
                },
            ]
        },
    }


def build_task_digest(snapshot, batches=None):
    """Plain-text preview of the same three-column daily task summary."""
    return build_task_notification(snapshot, batches)["text"]


def collect_summary(root, specs, *, now=None, service=None):
    """Read host scheduling evidence; DB failure cannot prevent local receipts being reported."""
    from sqlalchemy import event

    from index_valuation.config import Settings
    from index_valuation.db import build_engine
    from index_valuation.repository import Repository

    checked = (now or datetime.now(ZONE)).astimezone(ZONE)
    engine = None
    if service is None:
        repository = None
        try:
            settings = Settings(_env_file=Path(root) / ".env")
            engine = build_engine(settings)

            @event.listens_for(engine, "connect")
            def bounded_queries(connection, _record):
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '5000ms'")
                connection.commit()

            repository = Repository(engine)
        except Exception:
            # ScheduledTasks explicitly reports unavailable DB coverage and still reads receipts.
            pass
        service = ScheduledTasks(repository, specs, root=root, now=lambda: checked)
    try:
        snapshot = service.snapshot()
        batches = read_index_batches(root, specs, snapshot)
        return {
            "schema": SCHEMA,
            "date": checked.date().isoformat(),
            "checked_at": snapshot["checked_at"],
            "snapshot": snapshot,
            "index_batches": batches,
            **build_task_notification(snapshot, batches),
        }
    finally:
        if engine is not None:
            engine.dispose()


def send_task_summary(
    root, specs, settings, *, now=None, retry_date=None, collector=collect_summary, delivery=deliver
):
    """Freeze one Shanghai-day summary before delivery, including safe same-day retries."""
    root = Path(root)
    checked = (now or datetime.now(ZONE)).astimezone(ZONE)
    day = retry_date or checked.date()
    if not isinstance(day, date) or isinstance(day, datetime) or day > checked.date():
        raise ValueError("无效的摘要日期")
    if retry_date is None and checked.hour < 18:
        return {"status": "not_due", "date": day.isoformat()}
    if not settings.telegram_enabled:
        return {"status": "disabled"}
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {"status": "not_configured"}
    base = root / "data/scheduler/task-summaries"
    frozen = base / f"{day}.json"
    notification = root / "data/scheduler/notifications" / f"task-summary-{day}.json"
    with delivery_lock(frozen):
        if frozen.exists():
            summary = _read_object(frozen, base, maximum=1024 * 1024)
            if (
                summary.get("schema") != SCHEMA
                or summary.get("date") != day.isoformat()
                or not isinstance(summary.get("text"), str)
                or not summary["text"]
                or _time(summary.get("checked_at")) is None
                or _time(summary["checked_at"]).date() != day
                or ("rich_message" in summary and not isinstance(summary["rich_message"], dict))
            ):
                raise ValueError("已保存摘要无效")
        elif retry_date is not None:
            raise TelegramError("summary_not_found")
        elif notification.exists():
            # Missing original evidence must not generate different content for an existing send.
            raise TelegramError("summary_missing_for_delivery")
        else:
            summary = collector(root, specs, now=checked)
            save_json(frozen, summary)
    options = {"rich_message": summary["rich_message"]} if "rich_message" in summary else {}
    state = delivery(settings, summary["text"], notification, **options)
    return {
        "date": day.isoformat(),
        "checked_at": summary["checked_at"],
        **{key: state[key] for key in ("status", "message_ids", "error") if key in state},
    }
