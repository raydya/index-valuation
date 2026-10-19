"""Configure private delivery, preview saved runs, and retry known failed Telegram sends."""

import argparse
import json
import re
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import set_key

from index_valuation.notification_digest import build_digest
from index_valuation.registry import load_indices
from index_valuation.run_journal import read_reports
from index_valuation.telegram_notifications import (
    TelegramClient,
    TelegramError,
    TelegramSettings,
    deliver,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Telegram 私聊通知设置与验证（不打印 token）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("identity")
    commands.add_parser("enable")
    bind = commands.add_parser("bind")
    bind.add_argument(
        "--code", required=True, help="用户在私聊中发送的 /start index_valuation_<code>"
    )
    commands.add_parser("test")
    for name in ("preview", "send-latest"):
        item = commands.add_parser(name)
        item.add_argument("--market", choices=("asia", "us"), required=True)
    retry = commands.add_parser("retry")
    retry.add_argument("--batch-id", type=UUID, required=True)
    args = parser.parse_args(argv)
    client = None
    try:
        settings = TelegramSettings(_env_file=ROOT / ".env")
        if args.command == "status":
            print(
                json.dumps(
                    {
                        "enabled": settings.telegram_enabled,
                        "token_configured": bool(settings.telegram_bot_token),
                        "private_chat_configured": bool(settings.telegram_chat_id),
                        "proxy_configured": bool(settings.telegram_proxy_url),
                    }
                )
            )
            return 0
        if args.command in {"identity", "bind", "enable"}:
            client = TelegramClient(settings)
            if args.command == "identity":
                print(json.dumps(client.identity()))
            else:
                if args.command == "bind":
                    if not re.fullmatch(r"[A-Za-z0-9_-]{8,40}", args.code):
                        raise TelegramError("invalid_binding_code")
                    chat = client.binding_chat(args.code)
                else:
                    chat = settings.telegram_chat_id
                    if chat is None:
                        raise TelegramError("missing_private_chat")
                client.private_chat(chat)
                set_key(str(ROOT / ".env"), "TELEGRAM_CHAT_ID", str(chat))
                set_key(str(ROOT / ".env"), "TELEGRAM_ENABLED", "true")
                print("已绑定唯一私人会话，定时推送已启用。")
            return 0
        if args.command == "test":
            text = (
                "指数估值通知 · 接入测试\n\n"
                "Telegram 私聊推送已接通。\n"
                "北京时间每天 09:00 启动美股更新、17:00 启动A股和港股更新，任务结束后推送。\n"
                "通知包含任务结果、实际数据日期、PE、历史分位及估值区间；异常和缺口会单独标注。\n"
                "本条为通道测试，不代表已执行新的数据更新。"
            )
            path = ROOT / "data/scheduler/notifications" / f"test-{uuid4()}.json"
        elif args.command == "retry":
            path = ROOT / "data/scheduler/notifications" / f"{args.batch_id}.json"
            text = json.loads(path.read_text(encoding="utf-8"))["text"]
        else:
            receipt = json.loads(
                (ROOT / "data/scheduler/index-runs" / f"{args.market}.latest.json").read_text(
                    encoding="utf-8"
                )
            )
            if receipt.get("status") == "running" or not receipt.get("finished_at"):
                raise TelegramError("run_not_finished")
            batch = str(UUID(receipt["batch_id"]))
            journal = ROOT / "data/scheduler/index-runs" / f"{batch}.results.jsonl"
            reports = read_reports(journal, batch) if journal.exists() else []
            text = build_digest(load_indices(ROOT / "indices.toml"), receipt, reports)
            path = ROOT / "data/scheduler/notifications" / f"{batch}.json"
            if args.command == "preview":
                print(text)
                return 0
        result = deliver(settings, text, path)
        print(
            json.dumps(
                {key: result[key] for key in ("status", "message_ids", "error") if key in result}
            )
        )
        return 0 if result["status"] == "sent" else 2
    except TelegramError as error:
        print(json.dumps({"status": "failed", "error": error.kind}))
        return 2
    except Exception:
        # Pydantic and HTTP errors may otherwise include values or token-bearing URLs.
        print("配置或本地回执读取失败；请检查 .env 和批次回执，不要发送或打印 token。")
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
