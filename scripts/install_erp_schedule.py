"""Install the independent 08:00 Google Sheet connector job on macOS."""

import argparse
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

LABEL = "cn.index-valuation.erp-sheet"


def job_definition(root: Path, codex: Path, thread: str) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/run_erp_sync.py"),
            "--codex",
            str(codex),
            "--thread",
            thread,
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Hour": 8, "Minute": 0},
        "StandardOutPath": str(root / "logs/erp-sheet.stdout.log"),
        "StandardErrorPath": str(root / "logs/erp-sheet.stderr.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "ProcessType": "Background",
    }


def install_job(root: Path, generated: Path, target_dir: Path, domain: str) -> None:
    target = target_dir / generated.name
    original = target.read_bytes() if target.exists() else None
    if original:
        existing = plistlib.loads(original)
        if existing.get("WorkingDirectory") != str(root) or existing.get("ProgramArguments", [])[
            :2
        ] != [str(root / ".venv/bin/python"), str(root / "scripts/run_erp_sync.py")]:
            raise ValueError(f"已有同名任务不属于此表格同步程序，未覆盖：{target}")
    service = f"{domain}/{LABEL}"
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True, check=False)
    if loaded.returncode == 0:
        if not original:
            raise ValueError("同名服务已加载但缺少本地配置，未改动；请先核对任务归属。")
        subprocess.run(["launchctl", "bootout", service], check=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(generated.read_bytes())
    try:
        subprocess.run(["launchctl", "enable", service], check=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    except subprocess.CalledProcessError:
        if original:
            target.write_bytes(original)
            if loaded.returncode == 0:
                subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        else:
            target.unlink(missing_ok=True)
        raise
    print(f"已安装并启用：{target}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成/安装每天北京时间08:00的表格连接器同步任务")
    parser.add_argument("--thread", type=UUID, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if sys.platform != "darwin":
        parser.error("此安装器仅用于 macOS")
    if datetime.now().astimezone().utcoffset() != timedelta(hours=8):
        parser.error("launchd 按系统时区触发，请使用 Asia/Shanghai（UTC+8）")
    if not args.codex.is_absolute() or not os.access(args.codex, os.X_OK):
        parser.error("--codex 必须是可执行的绝对路径")
    for relative in (
        ".venv/bin/python",
        "scripts/run_erp_sync.py",
        "docs/erp-sheet-update-task.md",
    ):
        if not (root / relative).is_file():
            parser.error(f"缺少运行文件：{relative}")
    (root / "logs").mkdir(exist_ok=True)
    output = root / "data/scheduler" / f"{LABEL}.plist"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(plistlib.dumps(job_definition(root, args.codex, str(args.thread))))
    subprocess.run(["plutil", "-lint", str(output)], check=True)
    print(f"已生成：{output}")
    if args.install:
        install_job(root, output, Path.home() / "Library/LaunchAgents", f"gui/{os.getuid()}")
        print("每天08:00向指定 Codex 会话提交更新，使用 Google Drive 连接器；含周末。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
