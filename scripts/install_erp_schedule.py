"""Install the independent 08:00 Google Sheet connector job on macOS."""

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

LABEL = "cn.index-valuation.erp-sheet"


def write_plist(target, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def job_definition(root: Path, codex: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/run_erp_sync.py"),
            "--codex",
            str(codex),
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Hour": 8, "Minute": 0},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(root / "logs/erp-sheet.stdout.log"),
        "StandardErrorPath": str(root / "logs/erp-sheet.stderr.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "TZ": "Asia/Shanghai",
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
    if b"\n\tstate = running\n" in (getattr(loaded, "stdout", b"") or b""):
        raise ValueError("ERP 任务正在执行，未替换调度。")
    if loaded.returncode == 0:
        if not original:
            raise ValueError("同名服务已加载但缺少本地配置，未改动；请先核对任务归属。")
        backup = root / "data/scheduler/backups" / f"erp-{datetime.now():%Y%m%dT%H%M%S%f}"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / target.name).write_bytes(original)
        subprocess.run(["launchctl", "bootout", service], check=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        write_plist(target, generated.read_bytes())
        subprocess.run(["launchctl", "enable", service], check=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    except (OSError, subprocess.SubprocessError):
        if original:
            write_plist(target, original)
            if loaded.returncode == 0:
                subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        else:
            target.unlink(missing_ok=True)
        raise
    print(f"已安装并启用：{target}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成/安装每天北京时间08:00的表格连接器同步任务")
    parser.add_argument("--codex", type=Path, default=Path("/opt/homebrew/bin/codex"))
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
        "docs/erp-result.schema.json",
    ):
        if not (root / relative).is_file():
            parser.error(f"缺少运行文件：{relative}")
    (root / "logs").mkdir(exist_ok=True)
    output = root / "data/scheduler" / f"{LABEL}.plist"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(plistlib.dumps(job_definition(root, args.codex)))
    subprocess.run(["plutil", "-lint", str(output)], check=True)
    print(f"已生成：{output}")
    if args.install:
        install_job(root, output, Path.home() / "Library/LaunchAgents", f"gui/{os.getuid()}")
        print("每天08:00独立执行 Codex 更新并验证结果，使用 Google Drive 连接器；含周末。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
