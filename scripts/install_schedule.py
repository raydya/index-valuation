"""Generate or install separate daily Asia and US valuation jobs on macOS."""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LABEL = "cn.index-valuation.daily"
SCHEDULES = (("asia", 17, ("CN", "HK")), ("us", 9, ("US",)))


def job_definitions(root: Path) -> list[dict]:
    return [
        {
            "Label": f"{LABEL}.{name}",
            "ProgramArguments": [
                str(root / ".venv/bin/python"),
                str(root / "scripts/run_scheduled.py"),
                "--market",
                *markets,
            ],
            "WorkingDirectory": str(root),
            # Omitted weekday means every day, including weekends and holidays.
            "StartCalendarInterval": {"Hour": hour, "Minute": 0},
            "StandardOutPath": str(root / "logs" / f"daily.{name}.stdout.log"),
            "StandardErrorPath": str(root / "logs" / f"daily.{name}.stderr.log"),
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
            "ProcessType": "Background",
        }
        for name, hour, markets in SCHEDULES
    ]


def unload(domain: str, label: str) -> None:
    service = f"{domain}/{label}"
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True, check=False)
    if loaded.returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)


def install_jobs(root: Path, generated: list[Path], target_dir: Path, domain: str) -> None:
    legacy = target_dir / f"{LABEL}.plist"
    targets = [target_dir / path.name for path in generated]
    # Check every job before changing any; labels may be used by another checkout.
    for target in [*targets, legacy]:
        if target.exists():
            existing = plistlib.loads(target.read_bytes())
            if existing.get("WorkingDirectory") != str(root):
                raise ValueError(f"已有同名任务属于其他目录，未覆盖：{target}")
    target_dir.mkdir(parents=True, exist_ok=True)
    for source, target in zip(generated, targets, strict=True):
        unload(domain, target.stem)
        shutil.copyfile(source, target)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        print(f"已安装：{target}")
    # Retire the old all-market schedule only after both replacements are loaded.
    if legacy.exists():
        backup_dir = root / "data/scheduler/backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{LABEL}-{datetime.now():%Y%m%dT%H%M%S%f}.plist"
        shutil.copyfile(legacy, backup)
        unload(domain, LABEL)
        legacy.unlink()
        print(f"已停用旧任务，配置备份：{backup}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成北京时间17点中港、9点美股的 macOS 每日任务")
    parser.add_argument("--install", action="store_true", help="安装并加载当前用户的 LaunchAgent")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if sys.platform != "darwin":
        parser.error("该安装器用于 macOS；其他系统请定时运行 scripts/run_scheduled.py")
    if datetime.now().astimezone().utcoffset() != timedelta(hours=8):
        parser.error("LaunchAgent 按系统时区调度，请将系统时区设为 Asia/Shanghai")
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    output_dir = root / "data/scheduler"
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    for payload in job_definitions(root):
        path = output_dir / f"{payload['Label']}.plist"
        path.write_bytes(plistlib.dumps(payload))
        generated.append(path)
        print(f"已生成：{path}")
    if not args.install:
        return 0
    try:
        install_jobs(root, generated, Path.home() / "Library/LaunchAgents", f"gui/{os.getuid()}")
    except ValueError as error:
        parser.error(str(error))
    print("已启用：北京时间每天17:00更新A股和港股，每天09:00更新美股；核对各市场交易日历。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
