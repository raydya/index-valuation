import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from index_valuation.telegram_notifications import (
    TelegramClient,
    TelegramError,
    TelegramSettings,
    deliver,
    split_message,
)

TOKEN = "123456:" + "test_token_" * 4


def settings(**kwargs):
    return TelegramSettings(
        _env_file=None,
        telegram_bot_token=TOKEN,
        telegram_chat_id=123,
        telegram_enabled=True,
        **kwargs,
    )


def client(handler):
    return TelegramClient(settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_settings_hide_secrets_and_reject_nonprivate_destinations():
    value = settings()
    assert TOKEN not in repr(value)
    with pytest.raises(ValidationError):
        TelegramSettings(_env_file=None, telegram_chat_id=-123)
    secret = "invalid:private/secret"
    with pytest.raises(ValidationError) as error:
        TelegramSettings(_env_file=None, telegram_bot_token=secret)
    assert secret not in str(error.value)


def test_api_plain_text_private_delivery_and_receipt():
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("getChat"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": "private"}})
        assert body["text"] == "PE < 10 & 样本不足"
        assert body["chat_id"] == 123 and "parse_mode" not in body
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 5, "chat": {"id": 123}}}
        )

    api = client(handler)
    assert api.private_chat(123) == 123
    assert api.send(123, "PE < 10 & 样本不足") == 5
    assert len(requests) == 2


def test_http_info_and_debug_logs_redact_bot_token(caplog):
    caplog.set_level(logging.DEBUG)
    api = client(lambda _: httpx.Response(200, json={"ok": True, "result": {"is_bot": True}}))
    api.identity()
    logging.getLogger("httpcore.http11").debug("request '/bot%s/sendMessage'", TOKEN)
    assert TOKEN not in caplog.text
    assert "[telegram-token]" in caplog.text


@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
def test_rejects_nonprivate_chat(kind):
    api = client(
        lambda _: httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": kind}})
    )
    with pytest.raises(TelegramError, match="not_private_chat"):
        api.private_chat(123)


@pytest.mark.parametrize(
    "error_type,uncertain", [(httpx.ConnectError, False), (httpx.ReadTimeout, True)]
)
def test_transport_errors_do_not_expose_token_and_mark_ambiguous_send(error_type, uncertain):
    def fail(request):
        raise error_type(str(request.url), request=request)

    api = client(fail)
    with pytest.raises(TelegramError) as error:
        api.send(123, "test")
    assert TOKEN not in str(error.value)
    assert error.value.uncertain is uncertain


@pytest.mark.parametrize("code", [400, 401, 403, 409, 429, 500])
def test_telegram_error_body_is_never_exposed(code):
    api = client(
        lambda _: httpx.Response(code, json={"ok": False, "error_code": code, "description": TOKEN})
    )
    with pytest.raises(TelegramError) as error:
        api.send(123, "test")
    assert TOKEN not in str(error.value)
    assert error.value.uncertain is (code >= 500)


def test_binding_requires_unique_challenge_and_never_consumes_updates():
    seen = []

    def handler(request):
        seen.append(request.url.path.rsplit("/", 1)[-1])
        if seen[-1] == "getWebhookInfo":
            return httpx.Response(200, json={"ok": True, "result": {"url": ""}})
        assert "offset" not in json.loads(request.content)
        rows = [
            {
                "message": {
                    "text": "/start",
                    "chat": {"type": "private", "id": 999},
                    "from": {"id": 999},
                }
            },
            {
                "message": {
                    "text": "/start index_valuation_abcdefgh",
                    "chat": {"type": "private", "id": 123},
                    "from": {"id": 123},
                }
            },
        ]
        return httpx.Response(200, json={"ok": True, "result": rows})

    assert client(handler).binding_chat("abcdefgh") == 123
    assert seen == ["getWebhookInfo", "getUpdates"]


def test_existing_webhook_is_not_deleted_or_polled():
    seen = []

    def handler(request):
        seen.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"ok": True, "result": {"url": "https://existing.invalid"}})

    with pytest.raises(TelegramError, match="webhook_in_use"):
        client(handler).binding_chat("abcdefgh")
    assert seen == ["getWebhookInfo"]


def test_utf16_chunks_preserve_complete_unicode_message():
    text = "指数\n" + "🟢" * 4000
    parts = split_message(text)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts)


class FakeClient:
    def __init__(self, failures=None):
        self.sent, self.failures = [], failures or {}
        self.attempts = 0

    def private_chat(self, chat_id):
        assert chat_id == 123

    def send(self, chat_id, text):
        self.attempts += 1
        if error := self.failures.pop(self.attempts, None):
            raise error
        self.sent.append(text)
        return len(self.sent)

    def close(self):
        pass


def test_delivery_is_durable_and_confirmed_parts_not_repeated(tmp_path):
    api = FakeClient({2: TelegramError("rate_limited")})
    path, text = tmp_path / "receipt.json", "a" * 4000
    factory = lambda _: api  # noqa: E731
    assert deliver(settings(), text, path, client_factory=factory)["status"] == "failed"
    assert len(api.sent) == 1
    state = deliver(settings(), text, path, client_factory=factory)
    assert state["status"] == "sent" and len(api.sent) == 2
    assert "".join(api.sent) == text
    deliver(settings(), text, path, client_factory=factory)
    assert len(api.sent) == 2
    assert TOKEN not in path.read_text()


def test_ambiguous_delivery_and_crash_are_not_blindly_retried(tmp_path):
    api = FakeClient({1: TelegramError("transport_error", uncertain=True)})
    path = tmp_path / "receipt.json"
    factory = lambda _: api  # noqa: E731
    result = deliver(settings(), "test", path, client_factory=factory)
    assert result["status"] == "delivery_unknown"
    deliver(settings(), "test", path, client_factory=factory)
    assert api.attempts == 1
    result["status"] = "sending"
    path.write_text(json.dumps(result))
    assert deliver(settings(), "test", path, client_factory=factory)["status"] == "delivery_unknown"
    assert api.attempts == 1


def test_changed_content_or_recipient_cannot_reuse_batch_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    api = FakeClient()
    factory = lambda _: api  # noqa: E731
    deliver(settings(), "old", path, client_factory=factory)
    assert deliver(settings(), "new", path, client_factory=factory)["status"] == "conflict"
    changed = settings().model_copy(update={"telegram_chat_id": 456})
    assert deliver(changed, "old", path, client_factory=factory)["status"] == "conflict"
    assert len(api.sent) == 1


def test_disabled_notifications_do_not_touch_network(tmp_path):
    disabled = settings().model_copy(update={"telegram_enabled": False})
    assert deliver(
        disabled, "test", tmp_path / "r.json", client_factory=lambda _: pytest.fail("network")
    ) == {"status": "disabled"}


def test_notification_receipt_failure_does_not_change_collection_exit(tmp_path, monkeypatch):
    from test_schedule import runner

    original = runner.save_receipt
    calls = 0

    def save(path, record):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("disk error")
        original(path, record)

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "save_receipt", save)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 2})()
    )
    monkeypatch.setattr(runner, "notify_scheduled", lambda *a: {"status": "sent"})
    assert runner.main(["--market", "US"]) == 2


def test_scheduler_sends_partial_journal_after_timeout_without_changing_exit_code(
    tmp_path, monkeypatch
):
    from test_schedule import runner

    from index_valuation.run_journal import ResultJournal

    def run(command, **kwargs):
        path = Path(command[command.index("--result-journal") + 1])
        batch = command[command.index("--batch-id") + 1]
        with ResultJournal(path, batch) as journal:
            journal.append({"index": "sp500", "run_id": "completed-first-index"})
        raise runner.subprocess.TimeoutExpired(command, kwargs["timeout"])

    seen = []

    def notify(root, record, journal):
        assert record["status"] == "timed_out"
        assert record["run_ids"] == ["completed-first-index"]
        assert json.loads((root / "data/scheduler/index-runs/us.latest.json").read_text())[
            "finished_at"
        ]
        seen.append(record)
        raise RuntimeError("private-error-must-not-print")

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "notify_scheduled", notify)
    assert runner.main(["--market", "US"]) == 124
    record = json.loads((tmp_path / "data/scheduler/index-runs/us.latest.json").read_text())
    assert record["notification"] == {"status": "failed", "error": "notification_error"}
    assert seen and "private-error" not in json.dumps(record)
