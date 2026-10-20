"""Bridge the Mac's five investment schedules to a read-only container."""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from index_valuation.scheduled_tasks import ScheduledTasks, write_scheduler_snapshot

LABEL = "cn.index-valuation.schedule-status"
INTERVAL = 60


def job_definition(root: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/export_schedule_status.py"),
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "StartInterval": INTERVAL,
        "StandardOutPath": str(root / "logs/schedule-status.stdout.log"),
        "StandardErrorPath": str(root / "logs/schedule-status.stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "ProcessType": "Background",
    }


def _validate_existing(target: Path, root: Path):
    if target.is_symlink():
        raise ValueError("同名任务配置为符号链接，未修改")
    if not target.exists():
        return False
    existing = plistlib.loads(target.read_bytes())
    expected = job_definition(root)
    if any(
        existing.get(key) != expected[key]
        for key in ("Label", "WorkingDirectory", "ProgramArguments")
    ):
        raise ValueError("已有同名状态任务属于其他目录或命令，未修改")
    return True


def _loaded(domain: str) -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"{domain}/{LABEL}"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 113 or b"Could not find service" in result.stderr:
        return False
    raise ValueError("无法确认宿主状态任务的加载状态，未修改")


def install(root: Path, generated: Path, target_dir: Path, domain: str):
    target = target_dir / f"{LABEL}.plist"
    existed = _validate_existing(target, root)
    loaded = _loaded(domain)
    if loaded and not existed:
        raise ValueError("同名状态任务已加载但缺少可核对的 plist，未覆盖")
    old = target.read_bytes() if existed else None
    target_dir.mkdir(parents=True, exist_ok=True)
    if loaded:
        subprocess.run(["/bin/launchctl", "bootout", f"{domain}/{LABEL}"], check=True, timeout=5)
    try:
        shutil.copyfile(generated, target)
        subprocess.run(["/bin/launchctl", "bootstrap", domain, str(target)], check=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        if old is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(old)
            if loaded:
                subprocess.run(
                    ["/bin/launchctl", "bootstrap", domain, str(target)], check=True, timeout=5
                )
        raise


def uninstall(root: Path, target_dir: Path, domain: str):
    target = target_dir / f"{LABEL}.plist"
    if not _validate_existing(target, root):
        if _loaded(domain):
            raise ValueError("同名任务缺少可核对的 plist，未停用")
        return
    if _loaded(domain):
        subprocess.run(["/bin/launchctl", "bootout", f"{domain}/{LABEL}"], check=True, timeout=5)
    target.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="仅导出宿主调度状态；不会执行采集、表格或通知任务")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true", help="只生成供检查的独立状态任务 plist")
    mode.add_argument(
        "--install", action="store_true", help="安装每 60 秒导出状态的独立 LaunchAgent"
    )
    mode.add_argument("--uninstall", action="store_true", help="仅卸载本脚本的状态 LaunchAgent")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("此脚本只能在 macOS 宿主机导出 launchd 状态")
    root = args.root.resolve()
    try:
        if args.uninstall:
            uninstall(root, Path.home() / "Library/LaunchAgents", f"gui/{os.getuid()}")
            print("状态导出任务已卸载；原有五个任务及快照文件保持不变。")
            return 0
        if args.generate or args.install:
            (root / "logs").mkdir(parents=True, exist_ok=True)
            generated = root / "data/scheduler" / f"{LABEL}.plist"
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(plistlib.dumps(job_definition(root)))
            print(f"已生成：{generated}")
            if args.generate:
                return 0
        service = ScheduledTasks(None, {}, root=root)
        write_scheduler_snapshot(
            root / "data/scheduler/host-status/status.json", service.scheduler_metadata()
        )
        if args.install:
            install(root, generated, Path.home() / "Library/LaunchAgents", f"gui/{os.getuid()}")
            print("已安装独立状态任务：每 60 秒只读检查五个任务；不执行采集或发送消息。")
    except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException):
        # Do not log raw launchctl output or arbitrary plist values.
        print("状态快照或调度配置操作失败；请检查文件权限及同名任务归属。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
