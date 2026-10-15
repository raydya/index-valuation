"""Daily portfolio-price jobs, replacing the original one-time LaunchAgents."""

import argparse
import fcntl
import json
import os
import plistlib
import signal
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")
JOBS = {
    "us": {
        "hour": 9,
        "label": "cn.portfolio.quotes.daily.us",
        "legacy": "cn.portfolio.quotes.once.20261016.us0900",
        "prompt": "docs/portfolio-us-update-task.md",
    },
    "cn_hk_funds": {
        "hour": 17,
        "label": "cn.portfolio.quotes.daily.asia",
        "legacy": "cn.portfolio.quotes.once.20261016.ashk1700",
        "prompt": "docs/portfolio-asia-update-task.md",
    },
}


def eligibility(now: datetime, start_date: date, hour: int, attempted: bool) -> str:
    if now.date() < start_date:
        return "not_started"
    if now.hour < hour:
        return "not_due"
    return "already_attempted" if attempted else "due"


def run_job(root, key, codex, start_date, *, dry_run=False, now=None) -> int:
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    job = JOBS[key]
    base = root / "data/scheduled/portfolio-daily"
    run_dir = base / now.date().isoformat()
    state_path = run_dir / f"{key}.state.json"
    result_path = run_dir / f"{key}.result.md"
    prompt = f"实际执行时间：{now.isoformat()}。\n\n" + (root / job["prompt"]).read_text()
    command = [
        str(codex),
        "--no-daemon",
        "exec",
        "--approve-for-me",
        "-C",
        str(root),
        "--json",
        "--output-last-message",
        str(result_path),
        "-",
    ]
    status = eligibility(now, start_date, job["hour"], state_path.exists())
    if dry_run or status != "due":
        print(
            json.dumps(
                {
                    "job": key,
                    "date": now.date().isoformat(),
                    "start_date": str(start_date),
                    "hour": job["hour"],
                    "eligibility": status,
                    "will_execute": False,
                    "command": command,
                    "prompt_characters": len(prompt),
                },
                ensure_ascii=False,
            )
        )
        return 0
    os.umask(0o077)
    base.mkdir(parents=True, exist_ok=True)
    with (base / f"{key}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{key}: an update is still running; skipped.")
            return 0
        if state_path.exists():
            print(f"{key}: today's attempt already exists; skipped.")
            return 0
        run_dir.mkdir(exist_ok=True)
        record = {
            "job": key,
            "date": now.date().isoformat(),
            "started_at": now.isoformat(),
            "status": "attempt_started",
            "result_file": str(result_path),
        }
        with state_path.open("x") as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
        code = 1
        try:
            with (
                (run_dir / f"{key}.events.jsonl").open("wb") as out,
                (run_dir / f"{key}.stderr.log").open("wb") as err,
            ):
                child = subprocess.Popen(
                    command,
                    cwd=root,
                    stdin=subprocess.PIPE,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
                try:
                    child.communicate(input=prompt.encode(), timeout=3600)
                    code = child.returncode
                    record["status"] = "process_finished" if code == 0 else "process_failed"
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                    code = 124
                    record["status"] = "process_timed_out"
        except Exception as error:
            record["status"] = "runner_failed"
            record["error"] = str(error)
        finally:
            record["exit_code"] = code
            record["finished_at"] = datetime.now(ZONE).isoformat()
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(state_path)
            print(json.dumps(record, ensure_ascii=False))
        # A per-date receipt prevents duplicates; the LaunchAgent remains installed.
        return code


def definitions(root, codex, start_date):
    return [
        {
            "Label": job["label"],
            "ProgramArguments": [
                str(root / ".venv/bin/python"),
                str(root / "scripts/portfolio_schedule.py"),
                "run",
                key,
                "--codex",
                str(codex),
                "--start-date",
                str(start_date),
            ],
            "WorkingDirectory": str(root),
            "StartCalendarInterval": {"Hour": job["hour"], "Minute": 0},
            "RunAtLoad": False,
            "KeepAlive": False,
            "ProcessType": "Background",
            "EnvironmentVariables": {
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "Asia/Shanghai",
                "PYTHONUNBUFFERED": "1",
            },
            "StandardOutPath": str(root / f"logs/portfolio.{key}.stdout.log"),
            "StandardErrorPath": str(root / f"logs/portfolio.{key}.stderr.log"),
        }
        for key, job in JOBS.items()
    ]


def service_state(domain, label):
    return subprocess.run(
        ["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True, check=False
    )


def install_jobs(root, generated, target_dir, domain):
    new_targets = [target_dir / source.name for source in generated]
    legacy_targets = [target_dir / f"{job['legacy']}.plist" for job in JOBS.values()]
    snapshots = {}
    for target in [*new_targets, *legacy_targets]:
        data = target.read_bytes() if target.exists() else None
        service = service_state(domain, target.stem)
        expected = (
            root / "scripts/portfolio_schedule.py"
            if target in new_targets
            else root / "data/scheduled/portfolio-2026-10-16/run_once.py"
        )
        if data is not None:
            payload = plistlib.loads(data)
            if (
                payload.get("Label") != target.stem
                or payload.get("WorkingDirectory") != str(root)
                or payload.get("ProgramArguments", [])[:2]
                != [str(root / ".venv/bin/python"), str(expected)]
            ):
                raise ValueError(f"拒绝覆盖其他任务：{target}")
        elif service.returncode == 0:
            raise ValueError(f"同名服务已加载但无法核对归属：{target.stem}")
        if "\n\tstate = running\n" in service.stdout:
            raise ValueError(f"任务正在运行，未改动：{target.stem}")
        snapshots[target] = (data, service.returncode == 0)
    backup = root / "data/scheduler/backups" / f"portfolio-{datetime.now():%Y%m%dT%H%M%S%f}"
    backup.mkdir(parents=True, exist_ok=True)
    for target, (data, _) in snapshots.items():
        if data is not None:
            (backup / target.name).write_bytes(data)
    target_dir.mkdir(parents=True, exist_ok=True)
    changed = []
    try:
        for source, target in zip(generated, new_targets, strict=True):
            changed.append(target)
            if snapshots[target][1]:
                subprocess.run(["launchctl", "bootout", f"{domain}/{target.stem}"], check=True)
            target.write_bytes(source.read_bytes())
            subprocess.run(["launchctl", "enable", f"{domain}/{target.stem}"], check=True)
            subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
            if service_state(domain, target.stem).returncode:
                raise RuntimeError(f"新任务未加载：{target.stem}")
        # Only retire the one-time jobs after both daily replacements are loaded.
        for target in legacy_targets:
            changed.append(target)
            if snapshots[target][1]:
                subprocess.run(["launchctl", "bootout", f"{domain}/{target.stem}"], check=True)
            target.unlink(missing_ok=True)
    except Exception:
        for target in reversed(changed):
            if service_state(domain, target.stem).returncode == 0:
                subprocess.run(["launchctl", "bootout", f"{domain}/{target.stem}"], check=True)
            data, was_loaded = snapshots[target]
            if data is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(data)
                if was_loaded:
                    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        raise
    print(f"每日09:00、17:00任务已加载；原单次任务已停用，备份：{backup}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="每天通过Google Drive连接器同步持仓表报价")
    parser.add_argument("action", choices=("generate", "install", "run"))
    parser.add_argument("job", nargs="?", choices=tuple(JOBS))
    parser.add_argument("--codex", type=Path, default=Path("/opt/homebrew/bin/codex"))
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 10, 16))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if not args.codex.is_absolute() or not os.access(args.codex, os.X_OK):
        parser.error("Codex可执行文件不可用")
    if args.action == "run":
        if not args.job:
            parser.error("run需要指定us或cn_hk_funds")
        return run_job(root, args.job, args.codex, args.start_date, dry_run=args.dry_run)
    if args.job or args.dry_run:
        parser.error("job及--dry-run仅用于run")
    if sys.platform != "darwin" or datetime.now().astimezone().utcoffset() != timedelta(hours=8):
        parser.error("安装要求macOS及Asia/Shanghai（UTC+8）系统时区")
    for path in [root / ".venv/bin/python", *[root / j["prompt"] for j in JOBS.values()]]:
        if not path.is_file():
            parser.error(f"缺少运行文件：{path}")
    output = root / "data/scheduler"
    output.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    generated = []
    for job in definitions(root, args.codex, args.start_date):
        path = output / f"{job['Label']}.plist"
        path.write_bytes(plistlib.dumps(job))
        subprocess.run(["plutil", "-lint", str(path)], check=True)
        generated.append(path)
        print(json.dumps({"label": job["Label"], "calendar": job["StartCalendarInterval"]}))
    if args.action == "install":
        install_jobs(root, generated, Path.home() / "Library/LaunchAgents", f"gui/{os.getuid()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
