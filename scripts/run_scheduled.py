"""Launchd entry point with an upper runtime bound and a stable project directory."""

import subprocess
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    command = [str(root / ".venv/bin/index-valuation"), "update-index", "--all"]
    try:
        return subprocess.run(command, cwd=root, timeout=3600, check=False).returncode
    except subprocess.TimeoutExpired:
        print(
            "Daily valuation exceeded the one-hour deadline; inspect pipeline_runs and source logs."
        )
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
