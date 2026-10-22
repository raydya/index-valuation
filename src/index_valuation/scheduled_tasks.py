"""Read-only, bounded views of local investment schedules and receipts.

Execution receipts and current database coverage deliberately remain separate:
a manual rebuild is not evidence that launchd's last invocation succeeded.
"""

import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from .erp_results import validate_result

ZONE = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[2]
MAX_RECEIPT = 128 * 1024
MAX_LOG = 2 * 1024 * 1024
MAX_TEXT = 24_000
MAX_SCHEDULER_SNAPSHOT = 64 * 1024
SCHEDULER_SNAPSHOT_SCHEMA = "index_valuation_host_schedules_v1"


@dataclass(frozen=True)
class Task:
    id: str
    name: str
    label: str
    script: str
    description: str
    markets: tuple[str, ...] = ()
    portfolio_key: str | None = None


TASKS = (
    Task(
        "erp",
        "指数风险溢价表 ERP",
        "cn.index-valuation.erp-sheet",
        "run_erp_sync.py",
        "独立执行表格的指数 PE、盈利收益率与中美国债更新；运行状态和回读结果分别记录。",
    ),
    Task(
        "portfolio_us",
        "美股持仓报价",
        "cn.portfolio.quotes.daily.us",
        "portfolio_schedule.py",
        "更新持仓表美股及 ETF 报价，并回读公式及合计。",
        portfolio_key="us",
    ),
    Task(
        "index_us",
        "美股指数估值",
        "cn.index-valuation.daily.us",
        "run_scheduled.py",
        "按美国交易日更新指数点位、PE、隐含 EPS 和估值 band。",
        ("US",),
    ),
    Task(
        "policy_daily",
        "中国政策日报",
        "cn.index-valuation.policy-daily",
        "run_policy_daily.py",
        "检索中国市场相关政策，区分政策事实与市场影响判断，并保留来源、覆盖限制及推送结果。",
    ),
    Task(
        "index_asia",
        "中港指数估值",
        "cn.index-valuation.daily.asia",
        "run_scheduled.py",
        "按内地和香港交易日更新指数点位、PE、隐含 EPS 和估值 band。",
        ("CN", "HK"),
    ),
    Task(
        "portfolio_asia",
        "中港及基金持仓报价",
        "cn.portfolio.quotes.daily.asia",
        "portfolio_schedule.py",
        "更新持仓表 A 股、港股和基金报价，并回读公式及合计。",
        portfolio_key="cn_hk_funds",
    ),
)


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return ""
    # Return plain text, never HTML. Remove terminal control sequences as well.
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or ord(c) >= 32)[:limit]


def _datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(ZONE) if parsed.tzinfo else None


def _iso(value):
    parsed = _datetime(value)
    return parsed.isoformat() if parsed else None


def _day(value):
    if value is None:
        return None
    text = str(value)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strings(value):
    return (
        [_text(item, 500) for item in value[:30] if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _read(path, base, limit=MAX_RECEIPT, *, tail=False):
    """Never follow a receipt's paths or symlinks outside the approved directory."""
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError("文件位置不属于任务目录")
    try:
        with path.open("rb") as stream:
            if tail:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - limit))
            content = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(content) > limit:
        raise ValueError("文件超过读取上限")
    return content


def _json(path, base, warnings):
    try:
        raw = _read(path, base)
        if raw is None:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("回执必须为对象")
        return data
    except (OSError, ValueError, RecursionError):
        warnings.append(f"{path.name} 无法读取或格式无效")
        return None


def _launchctl(label):
    if label not in {task.label for task in TASKS}:
        raise ValueError("未知任务")
    return subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )


def _empty_run(status="never_run", summary="尚无执行记录"):
    return {
        "status": status,
        "started_at": None,
        "finished_at": None,
        "queued_at": None,
        "exit_code": None,
        "summary": summary,
        "result_text": "",
        "issues": [],
    }


def _receipt_run(receipt, allowed):
    status = receipt.get("status")
    if status not in allowed or not _iso(receipt.get("started_at")):
        return _empty_run("unknown", "执行回执字段不完整")
    return {
        **_empty_run(status),
        "started_at": _iso(receipt.get("started_at")),
        "finished_at": _iso(receipt.get("finished_at")),
        "queued_at": _iso(receipt.get("queued_at")),
        "exit_code": _integer(receipt.get("exit_code")),
        "summary": _text(receipt.get("summary"), 1000),
        "result_text": _text(receipt.get("result_text")),
        "issues": _strings(receipt.get("issues")),
    }


def _data_items(value):
    if not isinstance(value, list):
        return []
    return [
        {
            "name": _text(item.get("name"), 120),
            "date": _day(item.get("date")),
            "complete_date": _day(item.get("complete_date")),
            "status": _text(item.get("status"), 40),
            "note": _text(item.get("note"), 1000),
        }
        for item in value[:50]
        if isinstance(item, dict)
    ]


def _report_issues(name, report):
    if not isinstance(report, dict):
        return []
    issues = []
    if report.get("status") == "blocked":
        issues.append(f"{name}：采集暂停；{_text(report.get('reason'), 400)}")
    if report.get("status") == "failed":
        issues.append(f"{name}：本次处理失败，已有数据仍保留")
    collection = report.get("collection")
    collection = collection if isinstance(collection, dict) else report
    sources = collection.get("sources", {})
    if isinstance(sources, dict):
        for source, evidence in sources.items():
            if isinstance(evidence, dict) and evidence.get("status") in {"failed", "partial"}:
                provider, _, metric = str(source).rpartition("_")
                provider = {
                    "futu": "富途 OpenD",
                    "futorest": "富途 REST",
                    "csindex": "中证官方",
                    "baostock": "BaoStock",
                    "eastmoney": "东方财富",
                    "nasdaq": "Nasdaq 官方",
                    "fred": "FRED",
                    "xueqiu": "雪球",
                    "google": "Google",
                    "yahoo": "Yahoo",
                }.get(provider, _text(source, 80))
                metric = {"price": "价格", "pe": "PE", "pb": "PB", "ps": "PS"}.get(metric, "")
                status = "请求失败" if evidence["status"] == "failed" else "数据不完整"
                issues.append(f"{name}：{provider} {metric}{status}")
    if report.get("last_date") and report.get("latest_complete_date") != report["last_date"]:
        issues.append(f"{name}：最新数据日尚无完整隐含 EPS / band")
    return issues


def _run_ids(reports):
    ids = []
    for report in reports:
        for candidate in (report, report.get("collection", {})):
            if not isinstance(candidate, dict):
                continue
            try:
                ids.append(str(UUID(candidate.get("run_id", ""))))
            except (ValueError, TypeError, AttributeError):
                continue
    return list(dict.fromkeys(ids))[:100]


def _legacy_reports(content):
    """Only complete top-level CLI result arrays, never nested report objects."""
    decoder = json.JSONDecoder()
    batches = []
    candidates = list(re.finditer(r"(?m)^\[", content))[-100:]
    for match in candidates:
        try:
            value, _ = decoder.raw_decode(content, match.start())
        except (ValueError, RecursionError):
            continue
        if (
            isinstance(value, list)
            and value
            and all(
                isinstance(item, dict)
                and isinstance(item.get("index"), str)
                and item.get("mode") in {"direct_index", "direct_index_collection_paused"}
                for item in value
            )
        ):
            batches.append(value)
    return batches[-1] if batches else []


def write_scheduler_snapshot(path, payload):
    """Publish metadata atomically; neither credentials nor task results belong here."""
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(raw) > MAX_SCHEDULER_SNAPSHOT:
        raise ValueError("调度快照超过大小上限")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".host-status-", delete=False
        ) as f:
            temporary = Path(f.name)
            # This contains only allowlisted schedule metadata and is readable by
            # the unprivileged container user through a read-only bind mount.
            os.fchmod(f.fileno(), 0o644)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_scheduler_snapshot(path):
    """Reject symlinks, devices, FIFOs and unbounded input before parsing JSON."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SCHEDULER_SNAPSHOT:
            raise ValueError("调度快照不是有效的小型普通文件")
        raw = stream.read(MAX_SCHEDULER_SNAPSHOT + 1)
    if len(raw) > MAX_SCHEDULER_SNAPSHOT:
        raise ValueError("调度快照超过大小上限")
    return json.loads(raw)


class ScheduledTasks:
    def __init__(
        self,
        repository,
        specs,
        *,
        root=ROOT,
        home=None,
        now=None,
        launchctl=None,
        snapshot_file=None,
        snapshot_max_age_seconds=180,
    ):
        self.repository = repository
        self.specs = specs
        self.root = Path(root if root is not None else ROOT).resolve()
        self.home = Path(home) if home is not None else Path.home()
        self.now = now or (lambda: datetime.now(ZONE))
        self.launchctl = launchctl or _launchctl
        self.local_scheduler_available = sys.platform == "darwin" or launchctl is not None
        self.snapshot_file = Path(snapshot_file) if snapshot_file is not None else None
        if not isinstance(snapshot_max_age_seconds, int) or snapshot_max_age_seconds <= 0:
            raise ValueError("调度快照有效期必须为正整数秒")
        self.snapshot_max_age_seconds = snapshot_max_age_seconds

    def _scheduler(self, task, now, warnings):
        base = self.home / "Library/LaunchAgents"
        path = base / f"{task.label}.plist"
        scheduler = {"state": "unknown", "label": task.label, "last_exit_code": None, "runs": None}
        if not self.local_scheduler_available:
            warnings.append("当前系统无法查询 macOS 调度；请配置宿主调度快照")
            return scheduler, "调度时间未知", None
        payload = None
        try:
            raw = _read(path, base, 64 * 1024)
            if raw is not None:
                payload = plistlib.loads(raw)
                if (
                    not isinstance(payload, dict)
                    or payload.get("Label") != task.label
                    or payload.get("WorkingDirectory") != str(self.root)
                    or payload.get("ProgramArguments", [])[:2]
                    != [
                        str(self.root / ".venv/bin/python"),
                        str(self.root / "scripts" / task.script),
                    ]
                ):
                    warnings.append("同名调度配置与本项目不匹配，未读取其执行信息")
                    return scheduler, "配置归属待核对", None
                arguments = payload["ProgramArguments"][2:]
                if (
                    task.markets
                    and arguments != ["--market", *task.markets]
                    or task.portfolio_key
                    and arguments[:2] != ["run", task.portfolio_key]
                    or task.id == "policy_daily"
                    and arguments[:1] != ["run"]
                ):
                    warnings.append("调度命令的市场或持仓范围与任务不匹配")
                    return scheduler, "执行范围待核对", None
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
            warnings.append("调度配置无法读取或格式无效")
            return scheduler, "调度时间未知", None
        try:
            result = self.launchctl(task.label)
            if len(result.stdout) > 64 * 1024:
                raise ValueError("调度查询输出超限")
            if result.returncode == 0:
                state = re.search(r"(?m)^\s*state = ([^\n]+)$", result.stdout)
                scheduler["state"] = "running" if state and state[1] == "running" else "enabled"
                for field, key in (("last exit code", "last_exit_code"), ("runs", "runs")):
                    found = re.search(rf"(?m)^\s*{field} = (-?\d+)\s*$", result.stdout)
                    scheduler[key] = int(found[1]) if found else None
                if payload is None:
                    warnings.append("服务已加载但本地 plist 缺失，调度时间无法核对")
            elif "Could not find service" in result.stderr or result.returncode == 113:
                scheduler["state"] = "disabled" if payload else "not_installed"
            else:
                warnings.append("无法查询 launchd 状态；不影响已保存执行结果")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            warnings.append("launchd 查询不可用或超时；不影响已保存执行结果")
        interval = (payload or {}).get("StartCalendarInterval")
        if not isinstance(interval, dict) or set(interval) != {"Hour", "Minute"}:
            if payload:
                warnings.append("当前调度不是可识别的每日时刻")
            return scheduler, "调度时间未知", None
        hour, minute = _integer(interval.get("Hour")), _integer(interval.get("Minute"))
        if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
            warnings.append("调度时刻无效")
            return scheduler, "调度时间未知", None
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled <= now:
            scheduled += timedelta(days=1)
        next_run = scheduled.isoformat() if scheduler["state"] in {"enabled", "running"} else None
        return scheduler, f"每天 {hour:02d}:{minute:02d}（北京时间，含周末）", next_run

    def scheduler_metadata(self):
        """Export only host scheduler metadata; never query the DB or read receipts."""
        now = self.now().astimezone(ZONE)
        tasks = []
        for task in TASKS:
            warnings = []
            scheduler, schedule_text, next_run = self._scheduler(task, now, warnings)
            tasks.append(
                {
                    "id": task.id,
                    "scheduler": scheduler,
                    "schedule_text": schedule_text,
                    "next_run_at": next_run,
                    "warnings": warnings,
                }
            )
        return {
            "schema": SCHEDULER_SNAPSHOT_SCHEMA,
            "checked_at": now.isoformat(),
            "timezone": "Asia/Shanghai",
            "tasks": tasks,
        }

    def _load_host_metadata(self, now):
        try:
            payload = _read_scheduler_snapshot(self.snapshot_file)
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != SCHEDULER_SNAPSHOT_SCHEMA
                or payload.get("timezone") != "Asia/Shanghai"
            ):
                raise ValueError("invalid schema")
            checked = _datetime(payload.get("checked_at"))
            if checked is None:
                raise ValueError("invalid timestamp")
            rows = payload.get("tasks")
            if (
                not isinstance(rows, list)
                or len(rows) != len(TASKS)
                or any(
                    not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows
                )
                or {row["id"] for row in rows} != {task.id for task in TASKS}
            ):
                raise ValueError("invalid task identities")
            if checked > now:
                return payload, "宿主调度快照时间在未来，当前调度状态未知"
            if (now - checked).total_seconds() > self.snapshot_max_age_seconds:
                return payload, "宿主调度快照已过期，当前调度状态未知"
            return payload, None
        except FileNotFoundError:
            return None, "宿主调度快照尚未生成，当前调度状态未知"
        except (OSError, ValueError, TypeError, RecursionError):
            return None, "宿主调度快照无法读取或格式无效，当前调度状态未知"

    def _host_scheduler(self, task, now, payload, error, warnings):
        scheduler = {
            "state": "unknown",
            "label": task.label,
            "last_exit_code": None,
            "runs": None,
            "checked_at": _iso(payload.get("checked_at")) if payload else None,
            "source": "host_snapshot",
        }
        if error:
            warnings.append(error + "；已保存执行回执仍可查看")
            return scheduler, "调度时间未知", None
        try:
            row = next(row for row in payload["tasks"] if row["id"] == task.id)
            data = row["scheduler"]
            if (
                not isinstance(data, dict)
                or data.get("label") != task.label
                or data.get("state")
                not in {"unknown", "enabled", "running", "disabled", "not_installed"}
                or any(
                    data.get(field) is not None and _integer(data.get(field)) is None
                    for field in ("runs", "last_exit_code")
                )
                or (data.get("runs") is not None and data["runs"] < 0)
                or not isinstance(row.get("schedule_text"), str)
                or not isinstance(row.get("warnings"), list)
                or any(not isinstance(value, str) for value in row["warnings"])
            ):
                raise ValueError("invalid scheduler fields")
            next_run = _datetime(row.get("next_run_at"))
            if row.get("next_run_at") is not None and next_run is None:
                raise ValueError("invalid next occurrence")
            if next_run is not None:
                checked = _datetime(payload["checked_at"])
                if data["state"] not in {
                    "enabled",
                    "running",
                } or not checked < next_run <= checked + timedelta(days=1):
                    raise ValueError("invalid next occurrence")
                # A still-fresh snapshot can cross today's scheduled minute.
                if next_run <= now:
                    next_run += timedelta(days=1)
            scheduler.update({key: data.get(key) for key in ("state", "last_exit_code", "runs")})
            warnings.extend(_strings(row["warnings"]))
            return scheduler, _text(row["schedule_text"], 300), _iso(next_run)
        except (KeyError, ValueError, TypeError, StopIteration):
            warnings.append("宿主快照中的本任务身份或字段无效；已保存执行回执仍可查看")
            return scheduler, "调度时间未知", None

    def _index_run(self, task, scheduler, warnings, database_ok):
        key = "asia" if task.id == "index_asia" else "us"
        base = self.root / "data/scheduler/index-runs"
        path = base / f"{key}.latest.json"
        existed = path.exists()
        receipt = _json(path, base, warnings)
        reports = []
        allowed_keys = {s.key for s in self.specs.values() if s.market in task.markets}
        if receipt is not None:
            if receipt.get("markets") != list(task.markets):
                warnings.append("调度回执的市场范围不匹配")
                return _empty_run("unknown", "最新执行回执不属于此市场任务")
            run = _receipt_run(receipt, {"running", "finished", "partial", "failed", "timed_out"})
            started = _datetime(run["started_at"])
            finished = _datetime(run["finished_at"])
            valid = started and started <= self.now().astimezone(ZONE)
            if run["status"] != "running":
                valid = valid and finished and started <= finished <= self.now().astimezone(ZONE)
                valid = valid and run["exit_code"] is not None
            if run["status"] == "finished" and run["exit_code"] != 0:
                valid = False
            if not valid:
                return _empty_run("unknown", "执行回执缺少有效时间或退出码")
            ids = (
                _run_ids([{"run_id": value} for value in receipt.get("run_ids", [])[:100]])
                if (isinstance(receipt.get("run_ids"), list))
                else []
            )
            if run["status"] == "finished" and not ids:
                return _empty_run("unknown", "进程正常退出，但没有返回可核对的指数结果")
        elif existed:
            return _empty_run("unknown", "最新调度回执无法读取")
        else:
            log_base = self.root / "logs"
            try:
                raw = _read(log_base / f"daily.{key}.stdout.log", log_base, MAX_LOG, tail=True)
                reports = _legacy_reports(raw.decode("utf-8", errors="replace")) if raw else []
            except (OSError, ValueError, RecursionError):
                warnings.append("旧执行日志无法读取")
            if not reports:
                status = "unknown" if scheduler["runs"] else "never_run"
                return _empty_run(status, "尚无可核对的执行回执")
            reports = [report for report in reports if report["index"] in allowed_keys]
            if not reports:
                return _empty_run("unknown", "旧日志中没有属于此市场的执行结果")
            ids = _run_ids(reports)
            code = scheduler["last_exit_code"]
            statuses = {r.get("status") for r in reports}
            status = "partial" if statuses & {"partial", "failed", "blocked"} else "finished"
            if code == 124:
                status = "timed_out"
            elif code not in (None, 0, 2):
                status = "failed"
            elif code == 2:
                status = "partial"
            run = {**_empty_run(status, ""), "exit_code": code}
            warnings.append("本次结果来自旧调度日志；开始与完成时间按日志中的任务 ID 核对")
        if ids and database_ok:
            try:
                records = [
                    row
                    for row in self.repository.scheduled_run_records(ids)
                    if row.get("index_key") in allowed_keys
                ]
                if receipt is not None:
                    records = [
                        row
                        for row in records
                        if _datetime(row.get("started_at")) is not None
                        and _datetime(run["started_at"])
                        <= _datetime(row["started_at"])
                        <= (_datetime(run["finished_at"]) or self.now().astimezone(ZONE))
                    ]
                if not reports:
                    reports = [
                        row["report"] for row in records if isinstance(row.get("report"), dict)
                    ]
                if receipt is None:
                    starts = [_datetime(row.get("started_at")) for row in records]
                    finishes = [_datetime(row.get("finished_at")) for row in records]
                    run["started_at"] = _iso(min(filter(None, starts), default=None))
                    run["finished_at"] = _iso(max(filter(None, finishes), default=None))
            except Exception:
                warnings.append("数据库执行记录暂不可读；保留本地回执状态")
        for report in reports:
            key = report.get("index")
            name = self.specs[key].name if key in self.specs else "指数"
            run["issues"].extend(_report_issues(name, report))
        run["issues"] = list(dict.fromkeys(run["issues"]))[:30]
        if not run["summary"]:
            run["summary"] = {
                "running": "估值更新正在运行",
                "partial": "本轮部分完成；存在来源缺口或暂停项",
                "finished": "本轮采集及重建流程已返回；数据质量见各指数状态",
                "failed": "本轮执行失败；已入库数据保留",
                "timed_out": "执行超过时限，需检查已保存结果",
            }.get(run["status"], "本轮执行状态待核对")
        return run

    def _portfolio_run(self, task, warnings):
        base = self.root / "data/scheduled/portfolio-daily"
        try:
            paths = sorted(base.glob(f"????-??-??/{task.portfolio_key}.state.json"), reverse=True)[
                :90
            ]
        except OSError:
            warnings.append("持仓执行目录不可读")
            return _empty_run("unknown", "执行记录不可读")
        records = []
        corrupt_days = []
        for path in paths:
            data = _json(path, base, warnings)
            started = _datetime(data.get("started_at")) if data else None
            if not data or not started or data.get("job") != task.portfolio_key:
                corrupt_days.append(path.parent.name)
                continue
            records.append((started, path, data))
        if not records:
            return _empty_run("unknown" if paths else "never_run")
        started, path, data = max(records, key=lambda item: item[0])
        if any(day >= started.date().isoformat() for day in corrupt_days):
            return _empty_run("unknown", "最新持仓执行记录无效；未采用旧结果代替")
        statuses = {
            "attempt_started": "running",
            "process_finished": "finished",
            "process_failed": "failed",
            "runner_failed": "failed",
            "process_timed_out": "timed_out",
        }
        run = _receipt_run(
            {**data, "status": statuses.get(data.get("status"))}, set(statuses.values())
        )
        finished = _datetime(run["finished_at"])
        if started > self.now().astimezone(ZONE) or (
            run["status"] != "running"
            and (
                finished is None
                or not started <= finished <= self.now().astimezone(ZONE)
                or run["exit_code"] is None
            )
        ):
            return _empty_run("unknown", "持仓执行回执缺少有效时间或退出码")
        try:
            # Do not trust result_file from the receipt; use its fixed sibling.
            result = _read(path.with_name(f"{task.portfolio_key}.result.md"), base, MAX_TEXT)
            run["result_text"] = _text(result.decode("utf-8", errors="replace")) if result else ""
        except (OSError, ValueError):
            warnings.append("持仓结果文本不可读或超过大小上限")
        run["summary"] = {
            "running": "持仓报价任务已开始，等待完成回执",
            "finished": "执行进程已结束并返回结果；更新范围及缺口见执行结果",
            "failed": "持仓报价执行失败，需查看本次结果",
            "timed_out": "持仓报价执行超过一小时",
        }.get(run["status"], "执行状态待核对")
        if run["status"] == "finished" and (run["exit_code"] != 0 or not run["result_text"]):
            run["status"] = "unknown"
            run["summary"] = "进程已结束，但缺少可核对的结果文本或有效退出码"
        elif run["status"] == "finished":
            first_line = next(
                (line.strip() for line in run["result_text"].splitlines() if line.strip()), ""
            )
            run["summary"] = "结果摘要：" + first_line.lstrip("# ")[:300]
        return run

    def _policy_run(self, warnings):
        """Read the validated research receipt, never the model's raw execution log."""
        base = self.root / "data/scheduler/policy-daily"
        path = base / "last-execution.json"
        if not path.exists():
            return _empty_run()
        execution = _json(path, base, warnings)
        if execution is None:
            return _empty_run("unknown", "最新政策执行回执不可读，未采用旧结果代替")
        try:
            UUID(execution["run_id"])
            day = _day(execution["date"])
            started = _datetime(execution["started_at"])
            finished = _datetime(execution.get("finished_at"))
            status = execution["status"]
            exit_code = _integer(execution.get("exit_code"))
            now = self.now().astimezone(ZONE)
            if (
                execution.get("schema") != "policy_execution_v1"
                or execution.get("task") != "policy_daily"
                or status
                not in {"running", "completed", "partial", "failed", "timed_out", "interrupted"}
                or started is None
                or day != started.date().isoformat()
                or started > now
                or (
                    status == "running"
                    and (execution.get("finished_at") is not None or exit_code is not None)
                )
                or (
                    status != "running"
                    and (finished is None or not started <= finished <= now or exit_code is None)
                )
                or (status == "completed" and exit_code != 0)
                or (status in {"partial", "failed", "timed_out", "interrupted"} and exit_code == 0)
                or (
                    status in {"completed", "partial"}
                    and (
                        not _text(execution.get("summary")).strip()
                        or not _text(execution.get("result_text")).strip()
                    )
                )
            ):
                raise ValueError("执行状态字段无效")
            state = _json(base / "runs" / day / "state.json", base, warnings)
            identity = (
                "schema",
                "task",
                "date",
                "run_id",
                "status",
                "started_at",
                "finished_at",
                "exit_code",
            )
            if state is None or any(state.get(key) != execution.get(key) for key in identity):
                raise ValueError("本日执行记录不匹配")
        except (KeyError, TypeError, ValueError, AttributeError):
            warnings.append("政策执行回执身份、时间或本日记录不匹配，未作为完成依据")
            return _empty_run("unknown", "最新政策执行记录无效，未采用旧结果代替")
        run = _receipt_run(
            execution, {"running", "completed", "partial", "failed", "timed_out", "interrupted"}
        )
        run["date"] = day
        notification = execution.get("notification")
        if isinstance(notification, dict):
            value = notification.get("status")
            if value in {
                "sent",
                "failed",
                "delivery_unknown",
                "sending",
                "disabled",
                "not_configured",
                "pending",
                "not_sent",
                "skipped",
            }:
                run["notification_status"] = value
        return run

    def _erp_execution(self, base, execution, warnings):
        """A process outcome cannot stand in for a matched, verified business result."""
        try:
            UUID(execution["run_id"])
            day = _day(execution["date"])
            started = _datetime(execution["started_at"])
            finished = _datetime(execution.get("finished_at"))
            status = execution["status"]
            now = self.now().astimezone(ZONE)
            if (
                execution.get("schema") != "erp_execution_v1"
                or execution.get("task") != "erp"
                or execution.get("mode") != "codex_exec"
                or status
                not in {"running", "completed", "partial", "failed", "timed_out", "interrupted"}
                or started is None
                or day != started.date().isoformat()
                or started > now
                or (status == "running" and execution.get("finished_at") is not None)
                or (status != "running" and (finished is None or not started <= finished <= now))
            ):
                raise ValueError("执行状态字段无效")
        except (KeyError, TypeError, ValueError, AttributeError):
            return _empty_run("unknown", "最新 ERP 执行记录无效，未采用旧队列结果代替"), []
        run = _receipt_run(
            execution, {"running", "completed", "partial", "failed", "timed_out", "interrupted"}
        )
        if status == "interrupted":
            run["status"] = "failed"
        receipt = _json(base / "results" / f"{day}.json", base, warnings)
        clean = None
        if receipt is not None:
            try:
                if receipt.get("task") != "erp" or any(
                    receipt.get(key) != execution.get(key) for key in ("date", "run_id")
                ):
                    raise ValueError("业务回执身份不匹配")
                clean = validate_result(
                    receipt, not_before=started, now=now, require_all_items=True
                )
                if finished and _datetime(clean["finished_at"]) > finished:
                    raise ValueError("业务回执晚于执行结束")
            except (KeyError, TypeError, ValueError):
                clean = None
                warnings.append("ERP 业务回执不匹配或未通过验证，未作为完成依据")
        if clean is not None:
            run["result_text"] = _text(clean["result_text"])
            run["issues"] = _strings(clean["issues"])
            items = _data_items(clean["data_items"])
        else:
            items = []
        if status == "running":
            # The child can publish a receipt just before exiting; the parent
            # still owns the process outcome and must close the execution first.
            return run, items
        if status in {"failed", "timed_out", "interrupted"}:
            fallback = {
                "failed": "ERP 执行进程失败，需要核对实际表格结果。",
                "timed_out": "ERP 执行超过时限，写入结果可能未确认。",
                "interrupted": "ERP 执行进程已中断，写入结果可能未确认。",
            }[status]
            run["issues"].append(run["summary"] or fallback)
            if not run["summary"]:
                run["summary"] = fallback
            return run, items
        if clean is None or (status == "completed" and run["exit_code"] != 0):
            run["status"] = "unknown"
            run["summary"] = "执行已结束，但缺少匹配且有效的表格回读结果"
            return run, []
        run["status"] = clean["status"]
        if status == "partial" and clean["status"] == "completed":
            run["status"] = "partial"
            run["issues"].append(run["summary"] or "执行器报告部分完成，需要核对。")
        run["summary"] = _text(clean["summary"], 1000)
        return run, items

    def _erp_run(self, warnings):
        base = self.root / "data/scheduler/erp-sheet"
        execution_path = base / "last-execution.json"
        if execution_path.exists():
            execution = _json(execution_path, base, warnings)
            if execution is None:
                return _empty_run("unknown", "最新执行回执不可读；未采用旧队列结果代替"), []
            return self._erp_execution(base, execution, warnings)
        queue = _json(base / "last-queued.json", base, warnings)
        attempt = _json(base / "last-attempt.json", base, warnings)
        if (base / "last-attempt.json").exists() and attempt is None:
            return _empty_run("unknown", "最新提交回执不可读；未采用旧完成结果代替"), []
        if attempt and (
            not queue
            or (_datetime(attempt.get("queued_at")) or datetime.min.replace(tzinfo=ZONE))
            > (_datetime(queue.get("queued_at")) or datetime.min.replace(tzinfo=ZONE))
        ):
            run = _receipt_run(attempt, {"running", "queued", "failed", "timed_out"})
            if run["status"] == "timed_out":
                run["issues"].append("提交是否成功尚不明确；不要仅凭超时重复提交")
            return run, []
        if not queue:
            exists = (base / "last-queued.json").exists() or (base / "last-attempt.json").exists()
            return _empty_run("unknown" if exists else "never_run"), []
        day, queued = _day(queue.get("date")), _iso(queue.get("queued_at"))
        if not day or not queued or not isinstance(queue.get("thread"), str):
            return _empty_run("unknown", "队列回执字段不完整"), []
        run = {**_empty_run("queued", "已提交队列，尚无匹配的表格完成回执"), "queued_at": queued}
        receipt = _json(base / "results" / f"{day}.json", base, warnings)
        if receipt:
            matches = receipt.get("task") == "erp" and all(
                receipt.get(field) == queue.get(field) for field in ("date", "thread", "queued_at")
            )
            if matches and receipt.get("status") in {"completed", "partial", "failed"}:
                run = _receipt_run(receipt, {"completed", "partial", "failed"})
                times = [_datetime(run[key]) for key in ("queued_at", "started_at", "finished_at")]
                valid = (
                    all(times)
                    and times == sorted(times)
                    and times[-1] <= self.now().astimezone(ZONE)
                )
                if not valid:
                    return _empty_run("unknown", "完成回执时间缺失或顺序无效"), []
                items = _data_items(receipt.get("data_items"))
                if (
                    run["status"] in {"completed", "partial"}
                    and receipt.get("readback_verified") is not True
                ):
                    return _empty_run("unknown", "结果回执未确认表格回读验证"), []
                if run["status"] == "completed" and (
                    run["issues"]
                    or not items
                    or any(
                        item["status"] != "available"
                        or not item["date"]
                        or not item["complete_date"]
                        for item in items
                    )
                ):
                    return _empty_run("unknown", "完成状态与数据缺口记录不一致"), []
                return run, items
            warnings.append("已有 ERP 结果与本次队列标识不匹配，未作为完成依据")
        return run, []

    def _index_data(self, task, summaries):
        items = []
        for spec in self.specs.values():
            if spec.market not in task.markets:
                continue
            data = summaries.get(spec.key, {})
            latest = data.get("latest") or {}
            details = latest.get("details") or {}
            notes = ["数据库当前方法（可包含手动更新）"]
            status = "available" if latest.get("implied_eps") is not None else "partial"
            if not latest:
                status = "unknown"
                notes.append("暂无当前方法数据")
            elif latest.get("implied_eps") is None:
                notes.append("最新数据日缺少可用隐含 EPS / band")
            if not spec.collection_enabled:
                status = "paused"
                notes.append(spec.collection_disabled_reason)
            if details.get("basis_status") == "unconfirmed":
                notes.append("PE 财务口径尚未独立核实")
            processing = data.get("processing") or {}
            notes.extend(_report_issues(spec.name, processing.get("report")))
            items.append(
                {
                    "name": spec.name,
                    "date": _day(latest.get("trade_date")),
                    "complete_date": _day(data.get("complete_date")),
                    "status": status,
                    "note": _text("；".join(notes), 1500),
                }
            )
        return items

    def snapshot(self):
        now = self.now().astimezone(ZONE)
        warnings = []
        host_metadata, metadata_error = (
            self._load_host_metadata(now) if self.snapshot_file is not None else (None, None)
        )
        try:
            summaries = self.repository.scheduled_index_summaries(self.specs)
            database_ok = True
        except Exception:
            summaries, database_ok = {}, False
            warnings.append("数据库暂不可读；指数覆盖日期未知，本地调度和表格回执仍可查看")
        tasks = []
        for task in TASKS:
            task_warnings = []
            try:
                if self.snapshot_file is not None:
                    scheduler, schedule_text, next_run = self._host_scheduler(
                        task, now, host_metadata, metadata_error, task_warnings
                    )
                else:
                    scheduler, schedule_text, next_run = self._scheduler(task, now, task_warnings)
            except Exception:
                scheduler = {
                    "state": "unknown",
                    "label": task.label,
                    "last_exit_code": None,
                    "runs": None,
                }
                schedule_text, next_run = "调度时间未知", None
                task_warnings.append("本任务调度信息格式无效或暂不可读")
            items = []
            if task.markets:
                try:
                    items = self._index_data(task, summaries)
                except Exception:
                    task_warnings.append("本任务的数据库覆盖信息格式无效或暂不可读")
            try:
                if task.markets:
                    run = self._index_run(task, scheduler, task_warnings, database_ok)
                elif task.portfolio_key:
                    run = self._portfolio_run(task, task_warnings)
                elif task.id == "policy_daily":
                    run = self._policy_run(task_warnings)
                else:
                    run, items = self._erp_run(task_warnings)
            except Exception:
                # A syntactically valid JSON receipt can still have invalid field
                # types. Do not let one damaged task hide the remaining tasks.
                run = _empty_run("unknown", "本任务执行证据格式无效或暂不可读")
                task_warnings.append("未采用无效证据判断执行成功；其他任务仍可查看")
            scheduler_running = scheduler["state"] == "running" and run["status"] not in {
                "running",
                "queued",
            }
            scheduler_stopped = run["status"] == "running" and scheduler["state"] in {
                "enabled",
                "disabled",
                "not_installed",
            }
            if scheduler_running or scheduler_stopped:
                receipt_time = max(
                    filter(
                        None,
                        (_datetime(run[key]) for key in ("started_at", "finished_at", "queued_at")),
                    ),
                    default=None,
                )
                checked = _datetime(scheduler.get("checked_at"))
                # The host snapshot can still be fresh while a newer receipt has
                # already arrived through the read-only data mount. Such a sample
                # is not evidence that the newer execution stopped or is running.
                newer_receipt = (
                    self.snapshot_file is not None
                    and checked is not None
                    and receipt_time is not None
                    and checked < receipt_time
                )
                if newer_receipt:
                    task_warnings.append("调度状态采样早于最新执行回执；以更新的执行回执为准")
                elif scheduler_running:
                    run = _empty_run("running", "调度进程正在运行，等待本次执行回执")
                else:
                    run["status"] = "unknown"
                    run["summary"] = "任务已开始，但未发现运行进程或完成回执"
            if run["queued_at"] and run["status"] == "queued":
                minutes = int((now - _datetime(run["queued_at"])).total_seconds() / 60)
                if minutes >= 60:
                    task_warnings.append(
                        f"提交后已等待 {minutes // 60} 小时，仍未取得匹配的完成回执"
                    )
            tasks.append(
                {
                    "id": task.id,
                    "name": task.name,
                    "description": task.description,
                    "schedule_text": schedule_text,
                    "next_run_at": next_run,
                    "scheduler": scheduler,
                    "last_run": run,
                    "data_items": items,
                    "warnings": list(dict.fromkeys(task_warnings)),
                }
            )
        return {
            "checked_at": now.isoformat(),
            "timezone": "Asia/Shanghai",
            "tasks": tasks,
            "warnings": warnings,
        }
