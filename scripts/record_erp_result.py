"""Save a bounded ERP outcome tied to its execution, without writing Sheets."""

import argparse
import json
from pathlib import Path

from index_valuation.erp_results import save_result, timestamp  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="保存 ERP 表格执行结果供估值页面只读展示")
    parser.add_argument("--input", type=Path, required=True, help="结果 JSON 文件")
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > 128 * 1024:
            raise ValueError("结果文件过大")
        path = save_result(ROOT, json.loads(args.input.read_text()))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(str(error))
    print(f"结果已保存：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
