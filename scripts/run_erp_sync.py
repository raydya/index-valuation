"""Queue the authorized Google Drive connector task in an existing Codex session."""

import argparse
import fcntl
import json
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Asia/Shanghai")
PROBE = """这是每日08:00表格同步的连接器链路验证。
请仅使用 Google Drive 连接器读取以下表格的元数据及 Index ERP 的 A1:P10，
核对 sheetId=1665776241、PE/盈利收益率公式及中美国债输入是否可读。
https://docs.google.com/spreadsheets/d/17B0OcBwSrwkBrcM8BlEDNfbtNeL2ZsXpGX_HDr5b4kQ/edit?gid=1665776241
只做读取，不修改任何表格或调度，不使用 Google CLI（gws）或 Chrome。
简短向用户报告连接器验证结果；若失败，明确说明原因。"""


def save_attempt(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def queue_update(root: Path, codex: Path, thread: str, *, probe: bool = False) -> int:
    now = datetime.now(ZONE)
    state_dir = root / "data/scheduler/erp-sheet"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "last-queued.json"
    # A manual invocation and launchd must not submit the same daily task together.
    with (state_dir / "queue.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("ERP submission already in progress; skipped.", flush=True)
            return 0
        if not probe and state_path.exists():
            previous = json.loads(state_path.read_text())
            if previous.get("date") == now.date().isoformat() and previous.get("thread") == thread:
                print(
                    "Today's ERP task is already queued; sheet completion is in Codex.", flush=True
                )
                return 0
        identity = {"date": now.date().isoformat(), "queued_at": now.isoformat(), "thread": thread}
        message = (
            PROBE
            if probe
            else (
                f"提交时间：{now.isoformat()}。这是用户已授权的每日表格更新。\n"
                f"本次结果回执标识（原样保留）：{json.dumps(identity, ensure_ascii=False)}\n\n"
                + (root / "docs/erp-sheet-update-task.md").read_text()
            )
        )
        command = [str(codex), "queue", "--thread", thread, "--message", message]
        print(f"{now.isoformat()} submitting ERP {'probe' if probe else 'update'}", flush=True)
        attempt_path = state_dir / "last-attempt.json"
        attempt = {
            **identity,
            "status": "running",
            "started_at": now.isoformat(),
            "finished_at": None,
            "exit_code": None,
            "summary": "正在提交表格更新任务。",
        }
        if not probe:
            save_attempt(attempt_path, attempt)
        try:
            result = subprocess.run(
                command, cwd=root, capture_output=True, text=True, timeout=60, check=False
            )
        except subprocess.TimeoutExpired:
            if not probe:
                attempt.update(
                    status="timed_out",
                    finished_at=datetime.now(ZONE).isoformat(),
                    exit_code=124,
                    summary="提交回执超时，是否已排队未知；请先核对会话，避免重复写表。",
                )
                save_attempt(attempt_path, attempt)
            print(
                "Queue receipt timed out; submission status unknown. Check Codex before retrying."
            )
            return 124
        except OSError:
            if not probe:
                attempt.update(
                    status="failed",
                    finished_at=datetime.now(ZONE).isoformat(),
                    exit_code=1,
                    summary="无法启动表格任务提交程序，请检查 Codex 运行环境。",
                )
                save_attempt(attempt_path, attempt)
            return 1
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", flush=True)
        if result.returncode:
            if not probe:
                attempt.update(
                    status="failed",
                    finished_at=datetime.now(ZONE).isoformat(),
                    exit_code=result.returncode,
                    summary="表格任务提交失败，未记录成功排队。",
                )
                save_attempt(attempt_path, attempt)
            print(f"Queue failed with exit {result.returncode}; no successful receipt recorded.")
            return result.returncode
        if not probe:
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "status": "queued",
                        "date": now.date().isoformat(),
                        "queued_at": now.isoformat(),
                        "thread": thread,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            temporary.replace(state_path)
            attempt.update(
                status="queued",
                finished_at=datetime.now(ZONE).isoformat(),
                exit_code=0,
                summary="已排队，等待执行结果；排队成功不代表表格更新完成。",
            )
            save_attempt(attempt_path, attempt)
        print("Queued only; Google Drive readback and the result will appear in the Codex session.")
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="通过当前 Codex 会话的 Google Drive 连接器同步表格"
    )
    parser.add_argument("--thread", required=True, type=UUID, help="已有 Codex 会话 UUID")
    parser.add_argument("--codex", required=True, type=Path, help="Codex 可执行文件绝对路径")
    parser.add_argument("--probe", action="store_true", help="仅提交连接器只读验证，不更新表格")
    args = parser.parse_args(argv)
    if not args.codex.is_absolute() or not args.codex.is_file():
        parser.error("--codex 必须指向已有的绝对路径")
    return queue_update(ROOT, args.codex, str(args.thread), probe=args.probe)


if __name__ == "__main__":
    raise SystemExit(main())
