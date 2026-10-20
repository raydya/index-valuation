"""Validate ERP business results separately from the scheduled process outcome."""

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

EXPECTED_ERP_ITEMS = frozenset(
    {
        "沪深300",
        "中证A500",
        "科创50",
        "创业板指",
        "恒生科技",
        "SPY",
        "QQQ",
        "红利低波",
        "美国10年国债",
        "中国10年国债",
    }
)


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed


def validate_result(payload, *, not_before, now=None, require_all_items=False):
    """Return bounded business evidence; neither exit code nor prose implies success."""
    if not isinstance(payload, dict):
        raise ValueError("结果须为 JSON 对象")
    status = payload.get("status")
    if status not in {"completed", "partial", "failed"}:
        raise ValueError("结果状态须为 completed、partial 或 failed")
    verified = payload.get("readback_verified") is True
    if status in {"completed", "partial"} and not verified:
        raise ValueError("成功或部分完成须已回读验证；无法回读时记录 failed")
    times = [not_before, timestamp(payload["started_at"]), timestamp(payload["finished_at"])]
    if times != sorted(times):
        raise ValueError("时间顺序须为任务启动、结果开始、结果结束")
    if times[-1] > (now or datetime.now().astimezone()):
        raise ValueError("完成时间不能晚于当前时间")
    summary = payload.get("summary", "")
    result_text = payload.get("result_text", "")
    issues = payload.get("issues", [])
    items = payload.get("data_items", [])
    if not isinstance(summary, str) or not 1 <= len(summary) <= 1000:
        raise ValueError("请提供不超过1000字的结果摘要")
    if not isinstance(result_text, str) or len(result_text) > 20000:
        raise ValueError("结果正文过长")
    if (
        not isinstance(issues, list)
        or len(issues) > 50
        or any(not isinstance(item, str) or len(item) > 1000 for item in issues)
    ):
        raise ValueError("问题列表格式无效")
    if status == "completed" and issues:
        raise ValueError("存在未解决问题时应记录 partial")
    if not isinstance(items, list) or len(items) > 64:
        raise ValueError("数据日期列表格式无效")
    clean_items = []
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
        ):
            raise ValueError("每项数据须有名称")
        clean = {"name": item["name"][:120]}
        for key in ("date", "complete_date"):
            clean[key] = date.fromisoformat(item[key]).isoformat() if item.get(key) else None
        clean["status"] = item.get("status", "unknown")
        if clean["status"] not in {"available", "partial", "paused", "unknown"}:
            raise ValueError("数据状态无效")
        if not isinstance(item.get("note", ""), str):
            raise ValueError("数据说明须为文本")
        clean["note"] = item.get("note", "")[:1000]
        clean_items.append(clean)
    if status == "completed" and (
        not clean_items
        or any(
            item["status"] != "available" or not item["date"] or not item["complete_date"]
            for item in clean_items
        )
    ):
        raise ValueError("全部完成须提供各项已核验数据日期；有缺口请记录 partial")
    if (
        status == "completed"
        and require_all_items
        and (
            len(clean_items) != len(EXPECTED_ERP_ITEMS)
            or {item["name"] for item in clean_items} != EXPECTED_ERP_ITEMS
        )
    ):
        raise ValueError("全部完成须逐项列出全部十项 ERP 数据，不得缺项、重复或使用其他名称")
    return {
        "task": "erp",
        "started_at": payload["started_at"],
        "finished_at": payload["finished_at"],
        "status": status,
        "readback_verified": verified,
        "summary": summary,
        "issues": issues,
        "data_items": clean_items,
        "result_text": result_text,
    }


def _identity(folder, payload):
    execution_path = folder / "last-execution.json"
    if "run_id" in payload:
        if not isinstance(payload["run_id"], str):
            raise ValueError("执行标识须为 UUID 字符串")
        UUID(payload["run_id"])
        execution = json.loads(execution_path.read_text())
        if (
            not isinstance(execution, dict)
            or execution.get("schema") != "erp_execution_v1"
            or execution.get("task") != "erp"
            or execution.get("mode") != "codex_exec"
            or execution.get("status") != "running"
            or execution.get("finished_at") is not None
            or any(payload.get(key) != execution.get(key) for key in ("date", "run_id"))
        ):
            raise ValueError("回执与当前运行中的 ERP 执行不匹配，未写入")
        started = timestamp(execution["started_at"])
        if execution["date"] != started.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat():
            raise ValueError("执行日期与启动时间不一致")
        return (
            {key: payload[key] for key in ("date", "run_id")},
            started,
        )
    # Once direct execution exists, an old queued message cannot overwrite it.
    if execution_path.exists():
        raise ValueError("ERP 已切换为独立执行，拒绝旧排队回执覆盖执行结果")
    queued = json.loads((folder / "last-queued.json").read_text())
    keys = ("date", "thread", "queued_at")
    if not isinstance(queued, dict) or any(payload.get(key) != queued.get(key) for key in keys):
        raise ValueError("回执与最近排队任务不匹配，未写入")
    return {key: payload[key] for key in keys}, timestamp(payload["queued_at"])


def save_result(root, payload):
    """Save a verified result only for the exact currently active execution."""
    if not isinstance(payload, dict):
        raise ValueError("结果须为 JSON 对象")
    folder = Path(root) / "data/scheduler/erp-sheet"
    identity, not_before = _identity(folder, payload)
    day = date.fromisoformat(identity["date"]).isoformat()
    record = {
        **identity,
        **validate_result(payload, not_before=not_before, require_all_items="run_id" in identity),
    }
    destination = folder / "results" / f"{day}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".erp-result-", mode="w", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
