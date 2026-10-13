"""Generate or install the current user's macOS daily valuation job."""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LABEL = "cn.index-valuation.daily"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 macOS 21:00 / 22:00 的每日任务")
    parser.add_argument("--install", action="store_true", help="安装并加载当前用户的 LaunchAgent")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if sys.platform != "darwin":
        parser.error("该安装器用于 macOS；其他系统请定时运行 scripts/run_scheduled.py")
    if datetime.now().astimezone().utcoffset() != timedelta(hours=8):
        parser.error("LaunchAgent 按系统时区调度，请将系统时区设为 Asia/Shanghai")
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    generated = root / "data/scheduler" / f"{LABEL}.plist"
    generated.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/run_scheduled.py"),
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": [
            {"Weekday": weekday, "Hour": hour, "Minute": 0}
            for weekday in range(1, 6)
            for hour in (21, 22)
        ],
        "StandardOutPath": str(logs / "daily.stdout.log"),
        "StandardErrorPath": str(logs / "daily.stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "ProcessType": "Background",
    }
    generated.write_bytes(plistlib.dumps(payload))
    print(f"已生成：{generated}")
    if not args.install:
        return 0
    target = Path.home() / "Library/LaunchAgents" / generated.name
    domain = f"gui/{os.getuid()}"
    if target.exists():
        existing = plistlib.loads(target.read_bytes())
        if existing.get("WorkingDirectory") != str(root):
            parser.error(f"已有同名任务属于其他目录，未覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Unload only this project's previous job if present; unrelated tasks are untouched.
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True, check=False)
    shutil.copyfile(generated, target)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    print(f"已安装：{target}\n每个工作日 21:00 采集，22:00 重试；程序内核对交易日历。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
