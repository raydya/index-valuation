"""Save a bounded ERP outcome tied to the exact queued task, without writing Sheets."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed


def save_result(root, payload):
    if not isinstance(payload, dict):
        raise ValueError("结果须为 JSON 对象")
    folder = root / "data/scheduler/erp-sheet"
    queued = json.loads((folder / "last-queued.json").read_text())
    if any(payload.get(key) != queued.get(key) for key in ("date", "thread", "queued_at")):
        raise ValueError("回执与最近排队任务不匹配，未写入")
    day = date.fromisoformat(payload["date"]).isoformat()
    status = payload.get("status")
    if status not in {"completed", "partial", "failed"}:
        raise ValueError("结果状态须为 completed、partial 或 failed")
    verified = payload.get("readback_verified") is True
    if status in {"completed", "partial"} and not verified:
        raise ValueError("成功或部分完成须已回读验证；无法回读时记录 failed")
    times = [timestamp(payload[key]) for key in ("queued_at", "started_at", "finished_at")]
    if times != sorted(times):
        raise ValueError("时间顺序须为提交、开始、结束")
    if times[-1] > datetime.now().astimezone():
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
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
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
    record = {
        "task": "erp",
        **{
            key: payload[key]
            for key in ("date", "thread", "queued_at", "started_at", "finished_at")
        },
        "status": status,
        "readback_verified": verified,
        "summary": summary,
        "issues": issues,
        "data_items": clean_items,
        "result_text": result_text,
    }
    destination = folder / "results" / f"{day}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="保存 ERP 表格执行结果供估值页面只读展示")
    parser.add_argument("--input", type=Path, required=True, help="结果 JSON 文件")
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > 128 * 1024:
            raise ValueError("结果文件过大")
        path = save_result(ROOT, json.loads(args.input.read_text()))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    print(f"结果已保存：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
