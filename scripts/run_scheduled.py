"""Launchd entry point with an upper runtime bound and a stable project directory."""

import argparse
import subprocess
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按市场运行每日指数估值更新，最长一小时")
    parser.add_argument("--market", nargs="+", choices=("CN", "HK", "US"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    selection = ["--market", *args.market] if args.market else ["--all"]
    command = [str(root / ".venv/bin/index-valuation"), "update-index", *selection]
    try:
        return subprocess.run(command, cwd=root, timeout=3600, check=False).returncode
    except subprocess.TimeoutExpired:
        print(
            "Daily valuation exceeded the one-hour deadline; inspect pipeline_runs and source logs."
        )
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
