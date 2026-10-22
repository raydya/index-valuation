"""Manage the independent 18:00 summary notification LaunchAgent."""

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

LABEL = "cn.index-valuation.task-summary"
ROOT = Path(__file__).resolve().parents[1]


def job_definition(root: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/notify_task_summary.py"),
            "send",
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Hour": 18, "Minute": 0},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(root / "logs/task-summary.stdout.log"),
        "StandardErrorPath": str(root / "logs/task-summary.stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "TZ": "Asia/Shanghai"},
        "ProcessType": "Background",
    }


def _write(path: Path, content: bytes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".task-summary-", delete=False
        ) as f:
            temporary = Path(f.name)
            os.fchmod(f.fileno(), 0o644)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_existing(target: Path, root: Path):
    if target.is_symlink():
        raise ValueError("同名汇总任务为符号链接，未修改")
    if not target.exists():
        return None
    original = target.read_bytes()
    existing = plistlib.loads(original)
    expected = job_definition(root)
    if not isinstance(existing, dict) or any(
        existing.get(key) != expected[key]
        for key in ("Label", "WorkingDirectory", "ProgramArguments")
    ):
        raise ValueError("已有同名汇总任务属于其他目录或命令，未修改")
    return original


def _loaded(domain: str) -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"{domain}/{LABEL}"],
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 113 or b"Could not find service" in result.stderr:
        return False
    raise ValueError("无法确认汇总任务加载状态，未修改")


def install(root: Path, generated: Path, target_dir: Path, domain: str):
    content = generated.read_bytes()
    if plistlib.loads(content) != job_definition(root):
        raise ValueError("待安装的汇总任务配置与本项目不匹配")
    target = target_dir / f"{LABEL}.plist"
    original = _validate_existing(target, root)
    loaded = _loaded(domain)
    if loaded and original is None:
        raise ValueError("同名汇总任务已加载但没有可核对的 plist，未覆盖")
    target_dir.mkdir(parents=True, exist_ok=True)
    if loaded:
        subprocess.run(["/bin/launchctl", "bootout", f"{domain}/{LABEL}"], check=True, timeout=5)
    try:
        _write(target, content)
        subprocess.run(["/bin/launchctl", "bootstrap", domain, str(target)], check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        if original is None:
            target.unlink(missing_ok=True)
        else:
            _write(target, original)
            if loaded:
                subprocess.run(
                    ["/bin/launchctl", "bootstrap", domain, str(target)], check=True, timeout=5
                )
        raise


def uninstall(root: Path, target_dir: Path, domain: str):
    target = target_dir / f"{LABEL}.plist"
    original = _validate_existing(target, root)
    loaded = _loaded(domain)
    if original is None:
        if loaded:
            raise ValueError("同名汇总任务缺少可核对的 plist，未停用")
        return
    if loaded:
        subprocess.run(["/bin/launchctl", "bootout", f"{domain}/{LABEL}"], check=True, timeout=5)
    target.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成或安装每天北京时间18:00的六任务汇总通知")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--generate", action="store_true", help="仅生成 plist，默认行为")
    modes.add_argument("--install", action="store_true", help="安装独立的当前用户 LaunchAgent")
    modes.add_argument("--uninstall", action="store_true", help="只卸载18:00汇总通知任务")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("此安装器仅支持 macOS 宿主机")
    if not args.uninstall and datetime.now().astimezone().utcoffset() != timedelta(hours=8):
        parser.error("launchd 使用系统时区；请将系统时区设为 Asia/Shanghai（UTC+8）")
    root = args.root.resolve()
    try:
        target_dir = Path.home() / "Library/LaunchAgents"
        domain = f"gui/{os.getuid()}"
        if args.uninstall:
            uninstall(root, target_dir, domain)
            print("已卸载18:00汇总通知任务；六个业务任务保持不变。")
            return 0
        for relative in (".venv/bin/python", "scripts/notify_task_summary.py"):
            if not (root / relative).is_file():
                raise ValueError("缺少本机 Python 环境或汇总通知脚本")
        (root / "logs").mkdir(parents=True, exist_ok=True)
        generated = root / "data/scheduler" / f"{LABEL}.plist"
        generated.parent.mkdir(parents=True, exist_ok=True)
        _write(generated, plistlib.dumps(job_definition(root)))
        print(f"已生成：{generated}")
        if args.install:
            install(root, generated, target_dir, domain)
            print("已安装每天北京时间18:00的汇总通知；安装过程不发送消息。")
    except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException):
        print("汇总调度配置操作失败；请检查运行文件、权限和同名任务归属。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
