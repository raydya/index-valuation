"""Private Telegram delivery with durable receipts and conservative retry semantics."""

import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _TokenFilter(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r"\d+:[A-Za-z0-9_-]{20,}", "[telegram-token]", record.getMessage())
        record.args = ()
        return True


def _redact_http_logs():
    # HTTPX logs full URLs at INFO; httpcore child loggers emit request paths at DEBUG.
    names = {
        "httpx",
        "httpcore",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.connection",
        "httpcore.proxy",
        "httpcore.socks",
        *logging.Logger.manager.loggerDict,
    }
    for name in names:
        if name == "httpx" or name.startswith("httpcore"):
            logger = logging.getLogger(name)
            if not any(isinstance(item, _TokenFilter) for item in logger.filters):
                logger.addFilter(_TokenFilter())


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = Field(default=None, gt=0)
    telegram_proxy_url: SecretStr | None = None

    @field_validator("telegram_bot_token")
    @classmethod
    def token_format(cls, value):
        if value and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", value.get_secret_value()):
            raise ValueError("Telegram token 格式无效")
        return value

    @field_validator("telegram_proxy_url")
    @classmethod
    def proxy_format(cls, value):
        if value:
            url = urlsplit(value.get_secret_value())
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("Telegram 代理须为 HTTP(S) URL")
        return value


class TelegramError(Exception):
    """Messages are fixed locally: never expose request URLs containing the token."""

    def __init__(self, kind, *, uncertain=False):
        self.kind, self.uncertain = kind, uncertain
        super().__init__(kind)


class TelegramClient:
    def __init__(self, settings, *, client=None):
        if not settings.telegram_bot_token:
            raise TelegramError("missing_token")
        self._base = "https://api.telegram.org/bot" + settings.telegram_bot_token.get_secret_value()
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(20, connect=10),
            proxy=settings.telegram_proxy_url.get_secret_value()
            if settings.telegram_proxy_url
            else None,
            follow_redirects=False,
        )

    def close(self):
        self._client.close()

    def _call(self, method, payload=None):
        _redact_http_logs()
        sending = method in {"sendMessage", "sendRichMessage"}
        try:
            response = self._client.post(self._base + "/" + method, json=payload or {})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TelegramError("connection_failed") from None
        except httpx.HTTPError:
            raise TelegramError("transport_error", uncertain=sending) from None
        try:
            body = response.json()
        except ValueError:
            raise TelegramError("invalid_response", uncertain=sending) from None
        if not isinstance(body, dict):
            raise TelegramError("invalid_response", uncertain=sending)
        if not response.is_success or body.get("ok") is not True:
            code = body.get("error_code", response.status_code)
            kind = {
                400: "request_rejected",
                401: "invalid_token",
                403: "chat_forbidden",
                409: "bot_in_use",
                429: "rate_limited",
            }.get(code, "telegram_unavailable")
            raise TelegramError(
                kind, uncertain=sending and (not isinstance(code, int) or code >= 500)
            )
        return body.get("result")

    def identity(self):
        result = self._call("getMe")
        if not isinstance(result, dict) or result.get("is_bot") is not True:
            raise TelegramError("invalid_identity")
        return {"id": result.get("id"), "username": result.get("username")}

    def private_chat(self, chat_id):
        result = self._call("getChat", {"chat_id": chat_id})
        if (
            not isinstance(result, dict)
            or result.get("type") != "private"
            or result.get("id") != chat_id
        ):
            raise TelegramError("not_private_chat")
        return chat_id

    def binding_chat(self, code):
        """Read without offsets; never delete a webhook or acknowledge other consumers' updates."""
        info = self._call("getWebhookInfo")
        if not isinstance(info, dict) or info.get("url"):
            raise TelegramError("webhook_in_use")
        updates = self._call("getUpdates", {"timeout": 0, "limit": 100})
        if not isinstance(updates, list):
            raise TelegramError("invalid_response")
        chats = set()
        for update in updates:
            message = update.get("message", {}) if isinstance(update, dict) else {}
            chat, sender = message.get("chat", {}), message.get("from", {})
            if (
                message.get("text") == f"/start index_valuation_{code}"
                and chat.get("type") == "private"
                and isinstance(chat.get("id"), int)
                and chat["id"] > 0
                and sender.get("id") == chat["id"]
                and not sender.get("is_bot")
            ):
                chats.add(chat["id"])
        if len(chats) != 1:
            raise TelegramError("binding_not_found" if not chats else "ambiguous_binding")
        return chats.pop()

    def send(self, chat_id, text):
        result = self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}},
        )
        return self._sent_id(result, chat_id)

    def send_rich(self, chat_id, rich_message):
        result = self._call("sendRichMessage", {"chat_id": chat_id, "rich_message": rich_message})
        return self._sent_id(result, chat_id)

    @staticmethod
    def _sent_id(result, chat_id):
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("message_id"), int)
            or result.get("chat", {}).get("id") != chat_id
        ):
            raise TelegramError("invalid_send_receipt", uncertain=True)
        return result["message_id"]


def split_message(text, limit=3500):
    """Telegram's 4096 limit is respected even for astral Unicode characters."""
    parts, current, size = [], [], 0
    for character in text:
        units = len(character.encode("utf-16-le")) // 2
        if size + units > limit:
            parts.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += units
    if current:
        parts.append("".join(current))
    return parts


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def delivery_lock(path):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TelegramError("delivery_in_progress") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def deliver(settings, text, path, *, rich_message=None, client_factory=TelegramClient):
    """One receipt per batch; confirmed parts never repeat. Ambiguous sends require review."""
    if not settings.telegram_enabled:
        return {"status": "disabled"}
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {"status": "not_configured"}
    fingerprint = hashlib.sha256(
        f"{settings.telegram_bot_token.get_secret_value()}:{settings.telegram_chat_id}".encode()
    ).hexdigest()
    # Keep legacy text hashes unchanged; rich receipts freeze both content and layout.
    content = (
        text
        if rich_message is None
        else json.dumps(
            {"text": text, "rich_message": rich_message}, ensure_ascii=False, sort_keys=True
        )
    )
    digest = hashlib.sha256(content.encode()).hexdigest()
    parts = split_message(text) if rich_message is None else [rich_message]
    with delivery_lock(path):
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("content_hash") != digest or state.get("destination_hash") != fingerprint:
                return {"status": "conflict"}
            if state["status"] in {"sent", "sending", "delivery_unknown"}:
                if state["status"] == "sending":
                    state["status"] = "delivery_unknown"
                    save_json(path, state)
                return state
        else:
            state = {
                "status": "pending",
                "text": text,
                "content_hash": digest,
                "destination_hash": fingerprint,
                "message_ids": [],
                "parts": len(parts),
                **({"rich_message": rich_message} if rich_message is not None else {}),
            }
            save_json(path, state)
        client = client_factory(settings)
        try:
            client.private_chat(settings.telegram_chat_id)
            for offset, part in enumerate(parts[len(state["message_ids"]) :]):
                if offset:
                    time.sleep(1)
                state.update(status="sending", attempted_at=datetime.now(UTC).isoformat())
                save_json(path, state)
                message_id = (
                    client.send(settings.telegram_chat_id, part)
                    if rich_message is None
                    else client.send_rich(settings.telegram_chat_id, part)
                )
                state["message_ids"].append(message_id)
                state["status"] = "pending"
                save_json(path, state)
            state.update(status="sent", finished_at=datetime.now(UTC).isoformat())
            state.pop("error", None)
        except TelegramError as error:
            state.update(
                status="delivery_unknown" if error.uncertain else "failed", error=error.kind
            )
        finally:
            client.close()
        save_json(path, state)
        return state


def notify_scheduled(root, receipt, journal_path):
    """Independent of PostgreSQL/web availability, including failed collector processes."""
    from index_valuation.notification_digest import build_notification
    from index_valuation.registry import load_indices
    from index_valuation.run_journal import read_reports

    settings = TelegramSettings(_env_file=root / ".env")
    if not settings.telegram_enabled:
        return {"status": "disabled"}
    try:
        reports = read_reports(journal_path, receipt["batch_id"])
    except (OSError, ValueError):
        reports = []
    specs = load_indices(root / "indices.toml")
    message = build_notification(specs, receipt, reports)
    if not message["text"]:
        return {"status": "skipped", "error": "no_matching_indices"}
    path = root / "data/scheduler/notifications" / f"{receipt['batch_id']}.json"
    state = deliver(settings, message["text"], path, rich_message=message["rich_message"])
    return {key: state[key] for key in ("status", "message_ids", "error") if key in state}
