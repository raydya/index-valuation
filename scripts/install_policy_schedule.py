"""Manage the independent daily 16:00 China policy research LaunchAgent."""

import argparse
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

LABEL = "cn.index-valuation.policy-daily"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CODEX = Path("/opt/homebrew/bin/codex")
LAUNCHCTL = "/bin/launchctl"


def job_definition(root: Path, codex: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / ".venv/bin/python"),
            str(root / "scripts/run_policy_daily.py"),
            "run",
            "--codex",
            str(codex),
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Hour": 16, "Minute": 0},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(root / "logs/policy-daily.stdout.log"),
        "StandardErrorPath": str(root / "logs/policy-daily.stderr.log"),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "TZ": "Asia/Shanghai",
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "ProcessType": "Background",
    }


def _write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError("政策日报 plist 为符号链接，未修改")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".policy-daily-", delete=False
        ) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o644)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _validate_runtime(root: Path, codex: Path) -> None:
    if datetime.now().astimezone().utcoffset() != timedelta(hours=8):
        raise ValueError("launchd 按系统时区触发，请将系统时区设为 Asia/Shanghai（UTC+8）")
    if not codex.is_absolute() or not codex.is_file() or not os.access(codex, os.X_OK):
        raise ValueError("--codex 必须是可执行文件的绝对路径")
    for relative in (
        ".venv/bin/python",
        "scripts/run_policy_daily.py",
        "docs/policy-daily-task.md",
        "docs/policy-result.schema.json",
    ):
        if not (root / relative).is_file():
            raise ValueError(f"缺少政策日报运行文件：{relative}")
    if not os.access(root / ".venv/bin/python", os.X_OK):
        raise ValueError("项目 Python 不可执行")


def _validate_existing(target: Path, root: Path) -> bytes | None:
    if target.is_symlink():
        raise ValueError("同名政策日报任务为符号链接，未修改")
    if not target.exists():
        return None
    original = target.read_bytes()
    existing = plistlib.loads(original)
    arguments = existing.get("ProgramArguments") if isinstance(existing, dict) else None
    expected = job_definition(root, DEFAULT_CODEX)
    if (
        not isinstance(existing, dict)
        or existing.get("Label") != LABEL
        or existing.get("WorkingDirectory") != str(root)
        or existing.get("Program", expected["ProgramArguments"][0])
        != expected["ProgramArguments"][0]
        or not isinstance(arguments, list)
        or len(arguments) != 5
        or arguments[:4] != expected["ProgramArguments"][:4]
        or not isinstance(arguments[4], str)
        or not Path(arguments[4]).is_absolute()
        or not isinstance(existing.get("Disabled", False), bool)
    ):
        raise ValueError("已有同名政策日报任务属于其他目录或命令，未修改")
    return original


def _run(*arguments: str, check: bool = True):
    return subprocess.run(
        [LAUNCHCTL, *arguments], capture_output=True, text=True, check=check, timeout=10
    )


def _loaded(domain: str) -> str | None:
    result = _run("print", f"{domain}/{LABEL}", check=False)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 113 or "Could not find service" in result.stderr:
        return None
    raise ValueError("无法确认政策日报加载状态，未修改")


def _validate_loaded(output: str | None, root: Path, target: Path) -> None:
    if output is None:
        return
    arguments = re.search(r"^\s*arguments = \{\n(.*?)^\s*\}", output, re.MULTILINE | re.DOTALL)
    values = [line.strip() for line in arguments[1].splitlines()] if arguments else []
    expected = job_definition(root, DEFAULT_CODEX)["ProgramArguments"]
    if (
        not re.search(rf"^\s*path = {re.escape(str(target))}$", output, re.MULTILINE)
        or not re.search(rf"^\s*program = {re.escape(expected[0])}$", output, re.MULTILINE)
        or not re.search(rf"^\s*working directory = {re.escape(str(root))}$", output, re.MULTILINE)
        or len(values) != 5
        or values[:4] != expected[:4]
        or not Path(values[4]).is_absolute()
    ):
        raise ValueError("已加载同名政策日报任务的目录或命令不匹配，未修改")
    if re.search(r"^\s*state = running$", output, re.MULTILINE):
        raise ValueError("政策日报正在执行，未替换或卸载调度")


def _disabled(domain: str, original: bytes | None) -> bool:
    result = _run("print-disabled", domain)
    if "disabled services = {" not in result.stdout or "}" not in result.stdout:
        raise ValueError("无法确认政策日报启用状态，未修改")
    match = re.search(
        rf'^\s*"{re.escape(LABEL)}"\s*=>\s*(enabled|disabled|true|false)\s*$',
        result.stdout,
        re.MULTILINE,
    )
    if match:
        return match[1] in {"disabled", "true"}
    if f'"{LABEL}"' in result.stdout:
        raise ValueError("无法解析政策日报启用状态，未修改")
    return bool(plistlib.loads(original).get("Disabled", False)) if original else False


def _backup(root: Path, original: bytes | None) -> None:
    if original is not None:
        directory = root / "data/scheduler/backups"
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / f"policy-daily-{uuid.uuid4().hex}.plist", original)


def _restore(
    root: Path,
    target: Path,
    domain: str,
    original: bytes | None,
    was_loaded: bool,
    was_disabled: bool,
) -> None:
    current = _loaded(domain)
    _validate_loaded(current, root, target)
    if current is not None:
        _run("bootout", f"{domain}/{LABEL}")
    if original is None:
        target.unlink(missing_ok=True)
    else:
        _write(target, original)
    service = f"{domain}/{LABEL}"
    if was_loaded:
        _run("enable", service)
        _run("bootstrap", domain, str(target))
    _run("disable" if was_disabled else "enable", service)


def install(root: Path, codex: Path, generated: Path, target_dir: Path, domain: str) -> None:
    _validate_runtime(root, codex)
    content = generated.read_bytes()
    if plistlib.loads(content) != job_definition(root, codex):
        raise ValueError("待安装的政策日报配置与本项目不匹配")
    target = target_dir / f"{LABEL}.plist"
    original = _validate_existing(target, root)
    loaded = _loaded(domain)
    if loaded is not None and original is None:
        raise ValueError("同名政策日报已加载但缺少可核对的 plist，未覆盖")
    _validate_loaded(loaded, root, target)
    disabled = _disabled(domain, original)
    _backup(root, original)
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        if loaded is not None:
            _run("bootout", f"{domain}/{LABEL}")
        _write(target, content)
        _run("enable", f"{domain}/{LABEL}")
        _run("bootstrap", domain, str(target))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        try:
            _restore(root, target, domain, original, loaded is not None, disabled)
        except (OSError, ValueError, subprocess.SubprocessError) as rollback_error:
            raise ValueError(
                "政策日报安装失败且自动恢复未完成，请核对备份和 launchd 状态"
            ) from rollback_error
        raise error


def uninstall(root: Path, target_dir: Path, domain: str) -> None:
    target = target_dir / f"{LABEL}.plist"
    original = _validate_existing(target, root)
    loaded = _loaded(domain)
    if original is None:
        if loaded is not None:
            raise ValueError("同名政策日报缺少可核对的 plist，未停用")
        return
    _validate_loaded(loaded, root, target)
    disabled = _disabled(domain, original)
    _backup(root, original)
    try:
        if loaded is not None:
            _run("bootout", f"{domain}/{LABEL}")
        target.unlink()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        try:
            _restore(root, target, domain, original, loaded is not None, disabled)
        except (OSError, ValueError, subprocess.SubprocessError) as rollback_error:
            raise ValueError(
                "政策日报卸载失败且自动恢复未完成，请核对备份和 launchd 状态"
            ) from rollback_error
        raise error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成或安装每天北京时间16:00的中国政策日报")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--generate", action="store_true", help="仅生成 plist，默认行为")
    modes.add_argument("--install", action="store_true", help="安装独立的当前用户 LaunchAgent")
    modes.add_argument("--uninstall", action="store_true", help="只卸载16:00政策日报任务")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("此安装器仅支持 macOS 宿主机")
    root = args.root.resolve()
    try:
        target_dir = Path.home() / "Library/LaunchAgents"
        domain = f"gui/{os.getuid()}"
        if args.uninstall:
            uninstall(root, target_dir, domain)
            print("已卸载16:00政策日报任务。")
            return 0
        _validate_runtime(root, args.codex)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        generated = root / "data/scheduler" / f"{LABEL}.plist"
        generated.parent.mkdir(parents=True, exist_ok=True)
        _write(generated, plistlib.dumps(job_definition(root, args.codex)))
        print(f"已生成：{generated}")
        if args.install:
            install(root, args.codex, generated, target_dir, domain)
            print("已安装每天北京时间16:00的政策日报，含周末；安装时不执行任务或发送消息。")
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        plistlib.InvalidFileException,
    ) as error:
        print(f"政策日报调度配置操作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
