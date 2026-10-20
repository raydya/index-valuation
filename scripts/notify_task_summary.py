"""Preview, send, or retry the frozen daily five-task Telegram summary."""

import argparse
import json
from datetime import date
from pathlib import Path

from index_valuation.registry import load_indices
from index_valuation.task_digest import collect_summary, send_task_summary
from index_valuation.telegram_notifications import TelegramError, TelegramSettings

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="每天北京时间 18:00 汇总五项定时任务")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preview", help="只读预览当前状态，不发送、不冻结回执")
    commands.add_parser("send", help="冻结今天的任务结论并发送，同一天不重复推送")
    retry = commands.add_parser("retry", help="仅重试指定日期已冻结的摘要；发送不确定时不重发")
    retry.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        specs = load_indices(ROOT / "indices.toml")
        if args.command == "preview":
            print(collect_summary(ROOT, specs)["text"])
            return 0
        settings = TelegramSettings(_env_file=ROOT / ".env")
        result = send_task_summary(
            ROOT,
            specs,
            settings,
            retry_date=args.date if args.command == "retry" else None,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"sent", "not_due"} else 2
    except TelegramError as error:
        print(json.dumps({"status": "failed", "error": error.kind}, ensure_ascii=False))
        return 2
    except Exception:
        print("任务汇总读取或配置失败；请检查本地回执与配置，不要输出或发送凭据。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
