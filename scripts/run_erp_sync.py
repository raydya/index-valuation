"""Execute the authorized ERP connector task and persist a validated business receipt."""

import argparse
import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from index_valuation.codex_runner import execute_codex
from index_valuation.erp_results import save_result
from index_valuation.telegram_notifications import save_json

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Asia/Shanghai")


def run_update(root: Path, codex: Path, *, dry_run=False, now=None) -> int:
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    base = root / "data/scheduler/erp-sheet"
    run_dir = base / "runs" / now.date().isoformat()
    state_path = run_dir / "state.json"
    eligibility = (
        "not_due" if now.hour < 8 else "already_attempted" if state_path.exists() else "due"
    )
    if dry_run or eligibility == "not_due":
        print(
            json.dumps(
                {"date": str(now.date()), "eligibility": eligibility, "will_execute": False},
                ensure_ascii=False,
            )
        )
        return 0
    instructions = (root / "docs/erp-sheet-update-task.md").read_text()
    schema = root / "docs/erp-result.schema.json"
    json.loads(schema.read_text())
    previous_umask = os.umask(0o077)
    try:
        base.mkdir(parents=True, exist_ok=True)
        with (base / "execution.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("ERP execution is still running; skipped.")
                return 0
            if state_path.exists():
                previous = json.loads(state_path.read_text())
                if previous.get("status") == "running":
                    # The inherited lock also covers the child. Owning it now proves
                    # the previous process tree no longer owns this execution.
                    previous.update(
                        status="interrupted",
                        exit_code=1,
                        finished_at=datetime.now(ZONE).isoformat(),
                        summary="上次执行异常退出，未自动重复写表；实际结果须核对。",
                    )
                    save_json(state_path, previous)
                    save_json(base / "last-execution.json", previous)
                    print("Previous ERP execution was interrupted; not retried.")
                    return 1
                print("Today's ERP attempt already exists; skipped.")
                return 0
            run_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": "erp_execution_v1",
                "task": "erp",
                "mode": "codex_exec",
                "date": str(now.date()),
                "run_id": str(uuid4()),
                "started_at": now.isoformat(),
                "finished_at": None,
                "status": "running",
                "exit_code": None,
                "summary": "正在独立执行 ERP 表格更新，等待回读验证。",
            }

            def persist():
                save_json(state_path, record)
                save_json(base / "last-execution.json", record)

            try:
                persist()
                prompt = (
                    f"实际执行时间：{now.isoformat()}。本次执行标识：{record['run_id']}。\n"
                    "这是已授权的每日 ERP 更新修复后的实际执行，不是研究或排队任务。\n\n"
                    + instructions
                )
                (run_dir / "prompt.md").write_text(prompt)
                process = execute_codex(
                    root=root,
                    codex=codex,
                    prompt=prompt,
                    run_dir=run_dir,
                    schema=schema,
                    lock_fd=lock.fileno(),
                )
                finished = datetime.now(ZONE).isoformat()
                identity = {key: record[key] for key in ("date", "run_id", "started_at")}
                failure = {
                    **identity,
                    "finished_at": finished,
                    "status": "failed",
                    "readback_verified": False,
                    "data_items": [],
                    "summary": "不能确认 ERP 表格更新完成；执行失败或缺少有效回读结果。",
                    "issues": ["请先核对本次执行记录和表格；未自动重复写入。"],
                    "result_text": "本次没有取得可确认的表格完成回执，不能据进程退出判断更新成功。",
                }
                code = process["exit_code"]
                if code == 0:
                    try:
                        path = run_dir / "result.json"
                        if path.stat().st_size > 128 * 1024:
                            raise ValueError("result too large")
                        payload = json.loads(path.read_text())
                        if not isinstance(payload, dict):
                            raise ValueError("invalid result")
                        payload = {**payload, **identity, "finished_at": finished}
                        save_result(root, payload)
                        record["status"] = payload["status"]
                        record["summary"] = payload["summary"]
                        code = {"completed": 0, "partial": 2, "failed": 1}[payload["status"]]
                    except (OSError, ValueError, TypeError, KeyError):
                        save_result(root, failure)
                        record.update(status="failed", summary=failure["summary"])
                        code = 1
                else:
                    reason = {
                        "timed_out": "ERP 执行超过一小时，已终止进程组，表格写入结果待核对。",
                        "interrupted": "ERP 执行被中断，已终止进程组，表格写入结果待核对。",
                    }.get(process["status"], "ERP 执行进程失败，表格写入结果待核对。")
                    failure.update(summary=reason, issues=[reason, *failure["issues"]])
                    save_result(root, failure)
                    record.update(status=process["status"], summary=reason)
                record.update(
                    finished_at=finished, exit_code=code, process_exit_code=process["exit_code"]
                )
                persist()
                print(
                    json.dumps(
                        {
                            key: record[key]
                            for key in (
                                "date",
                                "run_id",
                                "started_at",
                                "finished_at",
                                "status",
                                "exit_code",
                            )
                        },
                        ensure_ascii=False,
                    )
                )
                return code
            except (OSError, ValueError, TypeError, KeyError):
                record.update(
                    status="failed",
                    exit_code=1,
                    finished_at=datetime.now(ZONE).isoformat(),
                    summary="ERP 执行或结果保存失败，表格是否已修改须核对；未自动重试。",
                )
                # Preserve failure evidence even when the business-result directory is broken.
                for target in (state_path, base / "last-execution.json"):
                    try:
                        save_json(target, record)
                    except OSError:
                        pass
                print("ERP execution or receipt persistence failed; no automatic retry.")
                return 1
    finally:
        os.umask(previous_umask)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="每天08:00独立执行 ERP Google Drive 连接器更新")
    parser.add_argument("--codex", type=Path, default=Path("/opt/homebrew/bin/codex"))
    parser.add_argument("--dry-run", action="store_true", help="只检查是否到期，不执行、不写回执")
    args = parser.parse_args(argv)
    if not args.codex.is_absolute() or not os.access(args.codex, os.X_OK):
        parser.error("--codex 必须是可执行的绝对路径")
    return run_update(ROOT, args.codex, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
