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
ORDER = ("index_us", "index_asia", "portfolio_us", "portfolio_asia", "erp")
LABELS = {
    "finished": "流程已返回",
    "completed": "完成（回执已确认回读）",
    "partial": "部分完成",
    "failed": "执行失败",
    "timed_out": "执行超时",
    "running": "仍在执行",
    "queued": "已提交队列，尚无匹配的完成回执",
    "unknown": "状态待确认",
    "never_run": "无执行回执",
}


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
        interval += "–" + _clock(finish, today) if finish else " 起，尚无结束回执"
        return interval
    if queued:
        return _clock(queued, today) + " 提交"
    return "时间未知"


def _portfolio_result(value):
    """Quote the runner's prose without interpreting a zero process exit as success."""
    if not isinstance(value, str):
        return ""
    lines = [
        line.strip().lstrip("# ")
        for line in value.splitlines()
        if line.strip() and not line.lstrip().startswith(("|", ":codex-file-citation"))
    ]
    text = _plain(" ".join(lines), 1200)
    return text if len(text) <= 600 else text[:600] + "…（摘要截断，完整结果见任务页面）"


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


def build_task_digest(snapshot, batches=None):
    """Render the five task conclusions; old receipts never become today's success."""
    checked = _time(snapshot.get("checked_at"))
    if checked is None:
        raise ValueError("缺少汇总检查时间")
    today = checked.date()
    batches = batches or {}
    rows = {row.get("id"): row for row in snapshot.get("tasks", []) if isinstance(row, dict)}
    task_names = {task.id: task.name for task in TASKS}
    lines = [
        f"每日任务汇总｜{today}",
        f"截至 {checked:%H:%M}（北京时间；计划每天 18:00 汇总）",
    ]
    for key in ORDER:
        row = _mapping(rows.get(key))
        run = _mapping(row.get("last_run"))
        schedule = _plain(row.get("schedule_text"), 200)
        match = re.search(r"每天 (\d{2}:\d{2})", schedule)
        planned = match[1] if match else "未知"
        name = _plain(row.get("name"), 100) or task_names[key]
        status = run.get("status")
        lines.extend(("", f"{name}｜计划 {planned}"))
        if not _belongs_today(run, today, checked):
            times = [value for value in _times(run).values() if value]
            if any(value > checked for value in times):
                lines.append("回执时间异常，今日执行结果待确认")
            elif status == "running" and not times:
                lines.append("调度显示正在运行，尚无本次时间回执")
            else:
                lines.append("今日未见执行回执")
                if times:
                    lines.append(
                        "最近记录 "
                        + _execution(run, today)
                        + "｜"
                        + LABELS.get(status, "状态待确认")
                    )
            warnings = row.get("warnings") or []
            if warnings:
                lines.append("提示：" + "；".join(_plain(item, 250) for item in warnings[:3]))
            continue
        label = (
            "进程已结束，数据结果见下"
            if key.startswith("portfolio_") and status == "finished"
            else LABELS.get(status, "状态待确认")
        )
        lines.append(f"实际 {_execution(run, today)}｜{label}")
        if key.startswith("portfolio_"):
            result = _portfolio_result(run.get("result_text"))
            lines.append("执行结果摘录：" + result if result else "尚无可核对的结果文本")
        elif key.startswith("index_"):
            batch = _mapping(batches.get(key))
            notified = {
                "sent": "原批次 Telegram 已发送",
                "failed": "原批次 Telegram 发送失败",
                "delivery_unknown": "原批次 Telegram 发送结果不确定",
                "sending": "原批次 Telegram 发送结果待确认",
                "disabled": "原批次 Telegram 推送未启用",
                "not_configured": "原批次 Telegram 未配置",
            }.get(batch.get("notification_status"))
            if notified:
                lines.append(notified)
            lines.extend(batch.get("lines") or ["本次批次明细不可用，数据完整性待确认"])
        else:
            if run.get("summary"):
                lines.append(_plain(run["summary"], 600))
            for item in row.get("data_items") or []:
                item = _mapping(item)
                lines.append(
                    f"{_plain(item.get('name'), 100)}：输入日期 {_day(item.get('date')) or '未知'}"
                )
            if run.get("result_text"):
                lines.append("执行结果摘录：" + _portfolio_result(run["result_text"]))
        notices = [*(run.get("issues") or []), *(row.get("warnings") or [])]
        if key.startswith("index_") and _mapping(batches.get(key)).get("batch_id"):
            # The journal already accounts for these results; DB-derived duplicates add noise.
            notices = row.get("warnings") or []
        if notices:
            lines.append("提示：" + "；".join(dict.fromkeys(_plain(item, 300) for item in notices)))
    for warning in snapshot.get("warnings") or []:
        lines.extend(("", _plain(warning, 600)))
    lines.extend(
        (
            "",
            "未完成任务按本次检查时刻如实记录，不等待其结束；"
            "指数完整性以本次批次为准，详细估值沿用原批次推送。",
        )
    )
    return "\n".join(lines)


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
            "text": build_task_digest(snapshot, batches),
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
    state = delivery(settings, summary["text"], notification)
    return {
        "date": day.isoformat(),
        "checked_at": summary["checked_at"],
        **{key: state[key] for key in ("status", "message_ids", "error") if key in state},
    }
