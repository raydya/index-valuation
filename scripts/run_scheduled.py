"""Launchd entry point with an upper runtime bound and a stable project directory."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Asia/Shanghai")


def save_receipt(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def result_ids(output):
    """Read complete top-level CLI reports, skipping provider console messages."""
    decoder = json.JSONDecoder()
    position, ids = 0, []
    while (start := output.find("{", position)) >= 0:
        try:
            value, length = decoder.raw_decode(output[start:])
        except ValueError:
            position = start + 1
            continue
        position = start + length
        if isinstance(value, dict) and value.get("mode", "").startswith("direct_index"):
            if value.get("run_id"):
                ids.append(value["run_id"])
    return ids


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按市场运行每日指数估值更新，最长一小时")
    parser.add_argument("--market", nargs="+", choices=("CN", "HK", "US"))
    args = parser.parse_args(argv)
    root = ROOT
    selection = ["--market", *args.market] if args.market else ["--all"]
    command = [str(root / ".venv/bin/index-valuation"), "update-index", *selection]
    market_key = {frozenset(("CN", "HK")): "asia", frozenset(("US",)): "us"}.get(
        frozenset(args.market or ()), "other"
    )
    path = root / "data/scheduler/index-runs" / f"{market_key}.latest.json"
    record = {
        "status": "running",
        "started_at": datetime.now(ZONE).isoformat(),
        "finished_at": None,
        "exit_code": None,
        "markets": args.market or ["CN", "HK", "US"],
        "run_ids": [],
        "summary": "正在采集并核验指数数据。",
    }
    save_receipt(path, record)
    code = 1
    # Keep full stdout for the existing launchd log; only a bounded tail is parsed
    # for report IDs. stderr remains live, and the receipt reports in-progress work.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        try:
            code = subprocess.run(
                command, cwd=root, timeout=3600, check=False, stdout=output
            ).returncode
            record["status"] = "finished" if code == 0 else "partial" if code == 2 else "failed"
            record["summary"] = {
                "finished": "采集流程已结束，逐项数据日期及完整性见下方。",
                "partial": "本次存在部分完成、来源缺口或暂停采集项。",
                "failed": "本次执行失败，已完成的指数可能仍有数据入库。",
            }[record["status"]]
        except subprocess.TimeoutExpired:
            code = 124
            record.update(status="timed_out", summary="执行超过一小时，已终止；请核对已入库结果。")
        except OSError:
            record.update(status="failed", summary="无法启动指数更新程序，请检查运行环境。")
        finally:
            output.seek(0)
            shutil.copyfileobj(output, sys.stdout)
            sys.stdout.flush()
            output.seek(0, 2)
            size = output.tell()
            output.buffer.seek(max(0, size - 8 * 1024 * 1024))
            record["run_ids"] = result_ids(output.buffer.read().decode("utf-8", errors="replace"))
            record.update(exit_code=code, finished_at=datetime.now(ZONE).isoformat())
            save_receipt(path, record)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
