"""Run a dated, read-only policy search, freeze its evidence, and deliver once."""

import argparse
import fcntl
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from index_valuation.codex_runner import execute_codex
from index_valuation.policy_digest import render_digest, validate_result
from index_valuation.telegram_notifications import (
    TelegramError,
    TelegramSettings,
    deliver,
    save_json,
)

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Asia/Shanghai")
SCHEMA = "policy_execution_v1"
MAX_REPORT = 512 * 1024


def read_object(path, base):
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("Policy report path outside task directory")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT + 1)
    if len(raw) > MAX_REPORT:
        raise ValueError("Policy report too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid policy report")
    return value


def window(now, *, preview=False, report_date=None):
    today = report_date or now.date()
    if (
        not isinstance(today, date)
        or isinstance(today, datetime)
        or today > now.date()
        or (today != now.date() and not preview)
    ):
        raise ValueError("Historical dates are only allowed for previews, never future dates")
    cutoff = datetime.combine(today, time(16), ZONE)
    if preview:
        cutoff = min(now, cutoff)
    return datetime.combine(today - timedelta(days=1), time(16), ZONE), cutoff


def _failure_text(record):
    return (
        f"中国政策日报｜{record['date']}\n"
        f"政策截止：{record['cutoff_at']}（北京时间）\n"
        f"{record['summary']}\n"
        "本次无法可靠判断是否出现类似 924 的政策，也无法确认市场影响方向。"
        "未将搜索或执行失败当作没有政策。"
    )


def _freeze(run_dir, record, payload=None):
    path = run_dir / "report.json"
    if path.exists():
        raise ValueError("Policy report is already frozen")
    save_json(
        path,
        {
            "schema": "policy_report_v1",
            **{
                key: record[key]
                for key in ("date", "run_id", "window_start", "cutoff_at", "finished_at")
            },
            "execution": dict(record),
            "result": payload,
            "text": record["result_text"],
        },
    )


def _restore_frozen(run_dir, record, base):
    """The frozen report stays authoritative across receipt-write or delivery failures."""
    report = read_object(run_dir / "report.json", base)
    evidence = report.get("execution")
    if (
        report.get("schema") != "policy_report_v1"
        or not isinstance(evidence, dict)
        or evidence.get("schema") != SCHEMA
        or evidence.get("task") != "policy_daily"
        or any(
            report.get(key) != record.get(key) or evidence.get(key) != record.get(key)
            for key in ("run_id", "date", "window_start", "cutoff_at")
        )
        or evidence.get("started_at") != record.get("started_at")
        or not evidence.get("finished_at")
        or evidence.get("finished_at") != report.get("finished_at")
    ):
        raise ValueError("Frozen policy report identity mismatch")
    payload = report.get("result")
    if payload is not None:
        context = {
            "report_date": date.fromisoformat(record["date"]),
            "window_start": datetime.fromisoformat(record["window_start"]),
            "cutoff_at": datetime.fromisoformat(record["cutoff_at"]),
        }
        payload = validate_result(payload, **context)
        text = render_digest(payload, **context)
        if (
            evidence.get("status") != payload["status"]
            or evidence.get("summary") != payload["summary"]
            or evidence.get("issues") != payload["issues"]
            or evidence.get("exit_code")
            != {"completed": 0, "partial": 2, "failed": 1}[payload["status"]]
        ):
            raise ValueError("Frozen policy business status mismatch")
    else:
        if evidence.get("status") not in {"failed", "timed_out", "interrupted"}:
            raise ValueError("Frozen policy report is missing research evidence")
        text = _failure_text(evidence)
    if report.get("text") != text or evidence.get("result_text") != text:
        raise ValueError("Frozen policy text mismatch")
    return {**evidence, "notification": record.get("notification", {"status": "not_sent"})}


def _notify(root, record, *, delivery=deliver, settings=None):
    try:
        settings = settings or TelegramSettings(_env_file=root / ".env")
        response = delivery(
            settings,
            record["result_text"],
            root / "data/scheduler/notifications" / f"policy-daily-{record['date']}.json",
        )
        record["notification"] = {
            key: response[key] for key in ("status", "message_ids", "error") if key in response
        }
    except TelegramError as error:
        record["notification"] = {
            "status": "delivery_unknown" if error.uncertain else "failed",
            "error": error.kind,
        }
    except Exception:
        # Do not log settings validation values or HTTP URLs containing credentials.
        record["notification"] = {"status": "failed", "error": "notification_failed"}


def run_daily(
    root,
    codex,
    *,
    now=None,
    dry_run=False,
    preview=False,
    report_date=None,
    executor=None,
    delivery=None,
):
    root = Path(root)
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    start, cutoff = window(now, preview=preview, report_date=report_date)
    report_day = cutoff.date()
    base = root / "data/scheduler/policy-daily"
    run_id = str(uuid4())
    run_dir = (
        base / "previews" / f"{now:%Y%m%dT%H%M%S}-{run_id}"
        if preview
        else base / "runs" / str(now.date())
    )
    state_path = run_dir / "state.json"
    eligibility = "not_due" if now.hour < 16 and not preview else "due"
    if state_path.exists():
        eligibility = "already_attempted"
    if dry_run or eligibility == "not_due":
        print(
            json.dumps(
                {
                    "date": str(report_day),
                    "eligibility": eligibility,
                    "window_start": start.isoformat(),
                    "cutoff_at": cutoff.isoformat(),
                    "will_execute": False,
                },
                ensure_ascii=False,
            )
        )
        return 0
    instructions = (root / "docs/policy-daily-task.md").read_text()
    schema = root / "docs/policy-result.schema.json"
    json.loads(schema.read_text())
    executor = executor or execute_codex
    delivery = delivery or deliver
    old_umask = os.umask(0o077)
    try:
        base.mkdir(parents=True, exist_ok=True)
        lock_path = base / ("preview.lock" if preview else "execution.lock")
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Policy research is already running; skipped.")
                return 0

            def persist(record):
                save_json(state_path, record)
                if not preview:
                    save_json(base / "last-execution.json", record)

            if state_path.exists():
                previous = read_object(state_path, base)
                if previous.get("status") == "running":
                    previous.update(
                        status="interrupted",
                        exit_code=130,
                        finished_at=datetime.now(ZONE).isoformat(),
                        summary="政策扫描上次执行中断，没有有效的完整结果。",
                        issues=["进程已退出但未保存终态；未自动重新搜索。"],
                    )
                    previous["result_text"] = _failure_text(previous)
                    # Preserve an already-frozen result if interruption happened before delivery.
                    frozen = run_dir / "report.json"
                    if frozen.exists():
                        previous = _restore_frozen(run_dir, previous, base)
                    else:
                        _freeze(run_dir, previous)
                    persist(previous)
                    _notify(root, previous, delivery=delivery)
                    persist(previous)
                    return previous["exit_code"] or (
                        0 if previous["notification"].get("status") == "sent" else 2
                    )
                print("Today's policy report already exists; use retry for delivery only.")
                return 0

            run_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": SCHEMA,
                "task": "policy_daily",
                "date": str(report_day),
                "run_id": run_id,
                "started_at": now.isoformat(),
                "finished_at": None,
                "window_start": start.isoformat(),
                "cutoff_at": cutoff.isoformat(),
                "status": "running",
                "exit_code": None,
                "summary": "正在检查官方政策发布并分析市场影响。",
                "result_text": "",
                "issues": [],
                "notification": {"status": "not_sent"},
            }
            try:
                persist(record)
                prompt = (
                    f"实际执行时间：{now.isoformat()}\n报告日期：{report_day}\n"
                    f"查询窗口起点：{start.isoformat()}（含）\n"
                    f"政策截止时刻：{cutoff.isoformat()}（含）\n"
                    f"模式：{'预览，不推送，不占用今日定时任务' if preview else '每日正式报告'}\n\n"
                    + instructions
                )
                if report_day < now.date():
                    prompt += (
                        "\n\n本次为历史回溯试跑。只纳入截至上述政策截止时刻已经公开的事实；"
                        "不能用后续文件或行情解释当时尚未知的信息。实际检索发生在今天，"
                        "无法证明搜索索引或已更新网页在历史时刻可见，须说明这一回溯限制。"
                    )
                (run_dir / "prompt.md").write_text(prompt)
                process = executor(
                    root=root,
                    codex=codex,
                    prompt=prompt,
                    run_dir=run_dir,
                    schema=schema,
                    lock_fd=lock.fileno(),
                    timeout=2400,
                    research_only=True,
                )
                record["finished_at"] = datetime.now(ZONE).isoformat()
                record["process_exit_code"] = process["exit_code"]
                payload = None
                if process["exit_code"] == 0:
                    try:
                        payload = validate_result(
                            read_object(run_dir / "result.json", base),
                            report_date=report_day,
                            window_start=start,
                            cutoff_at=cutoff,
                        )
                        record.update(
                            status=payload["status"],
                            summary=payload["summary"],
                            issues=payload["issues"],
                            result_text=render_digest(
                                payload,
                                report_date=report_day,
                                window_start=start,
                                cutoff_at=cutoff,
                            ),
                            exit_code={"completed": 0, "partial": 2, "failed": 1}[
                                payload["status"]
                            ],
                        )
                    except (OSError, ValueError, TypeError, KeyError):
                        record.update(
                            status="failed",
                            exit_code=1,
                            summary="政策扫描没有返回通过来源、日期及结构校验的报告。",
                            issues=["结构化结果缺失或无效，未将进程退出成功当作研究完成。"],
                        )
                else:
                    status = process["status"]
                    if status not in {"failed", "timed_out", "interrupted"}:
                        status = "failed"
                    record.update(
                        status=status,
                        exit_code={"failed": 1, "timed_out": 124, "interrupted": 130}[status],
                        summary={
                            "failed": "政策扫描执行失败。",
                            "timed_out": "政策扫描超过40分钟，已终止本次执行。",
                            "interrupted": "政策扫描执行被中断。",
                        }[status],
                        issues=["本次没有可确认的完整政策研究结果。"],
                    )
                if not record["result_text"]:
                    record["result_text"] = _failure_text(record)
                _freeze(run_dir, record, payload)
                persist(record)
                if preview:
                    record["notification"] = {"status": "preview_only"}
                else:
                    _notify(root, record, delivery=delivery)
                persist(record)
                print(
                    json.dumps(
                        {
                            **{
                                key: record[key]
                                for key in ("date", "status", "exit_code", "notification")
                            },
                            "report": str(run_dir / "report.json"),
                            "preview": preview,
                        },
                        ensure_ascii=False,
                    )
                )
                return record["exit_code"] or (
                    0 if preview or record["notification"].get("status") == "sent" else 2
                )
            except (OSError, ValueError, TypeError, KeyError):
                if (run_dir / "report.json").exists():
                    # Once frozen, a transient receipt-write failure must not turn
                    # valid research into contradictory text that cannot be retried.
                    try:
                        record = _restore_frozen(run_dir, record, base)
                        record["persistence_issue"] = (
                            "报告已冻结；执行回执保存曾失败，请检查通知状态。"
                        )
                        persist(record)
                    except (OSError, ValueError, TypeError, KeyError):
                        pass
                    print(
                        "Frozen policy report preserved; inspect delivery and retry it if needed."
                    )
                    return 1
                record.update(
                    status="failed",
                    exit_code=1,
                    finished_at=datetime.now(ZONE).isoformat(),
                    summary="政策报告执行或保存失败，请检查本机回执。",
                    issues=["结果未能完整保存，未自动重跑或重新发送。"],
                )
                record["result_text"] = _failure_text(record)
                try:
                    persist(record)
                except OSError:
                    pass
                print("Policy execution or receipt persistence failed.")
                return 1
    finally:
        os.umask(old_umask)


def retry_delivery(root, day, *, now=None, delivery=None):
    root = Path(root)
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    if day > now.date():
        raise ValueError("Cannot retry a future report")
    base = root / "data/scheduler/policy-daily"
    run_dir = base / "runs" / str(day)
    with (base / "execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = read_object(run_dir / "state.json", base)
        if record.get("schema") != SCHEMA or record.get("date") != str(day):
            raise ValueError("Frozen policy report does not match execution receipt")
        # Also recover yesterday's report if it froze before a crash. The shared
        # execution lock proves no live research process still owns this state.
        record = _restore_frozen(run_dir, record, base)
        save_json(run_dir / "state.json", record)
        _notify(root, record, delivery=delivery or deliver)
        save_json(run_dir / "state.json", record)
        latest = base / "last-execution.json"
        if latest.exists() and read_object(latest, base).get("run_id") == record["run_id"]:
            save_json(latest, record)
        print(json.dumps(record["notification"], ensure_ascii=False))
        return 0 if record["notification"].get("status") == "sent" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="每天16:00检查中国政策并推送市场影响日报")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "preview"):
        action = commands.add_parser(
            name, help="正式执行并推送" if name == "run" else "实际搜索预览，不推送、不占用定时任务"
        )
        action.add_argument("--codex", type=Path, default=Path("/opt/homebrew/bin/codex"))
        action.add_argument("--dry-run", action="store_true", help="仅检查窗口，不执行搜索")
        if name == "preview":
            action.add_argument(
                "--date", type=date.fromisoformat, help="回看指定日期16:00，保留真实执行时间"
            )
    retry = commands.add_parser("retry", help="仅重试已冻结正式报告的推送，不重新搜索")
    retry.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "retry":
            return retry_delivery(ROOT, args.date)
        if not args.codex.is_absolute() or not os.access(args.codex, os.X_OK):
            parser.error("--codex 必须为可执行的绝对路径")
        return run_daily(
            ROOT,
            args.codex,
            dry_run=args.dry_run,
            preview=args.command == "preview",
            report_date=getattr(args, "date", None),
        )
    except (OSError, ValueError, TypeError, KeyError):
        print("政策扫描配置或回执读取失败；未输出凭据，也未重新搜索或覆盖旧报告。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
