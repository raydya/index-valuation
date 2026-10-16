import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError
from index_valuation.providers.google_history import GoogleHistory, parse_history
from index_valuation.registry import load_indices

SPEC = load_indices()["ustotal"]
DAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 16, 1, tzinfo=UTC)
BAR = [5469.69, 5494.37, 5499.65, 5435.44, "2026-10-15T16:00:00-04:00", 0]


def page(*, bars=None, identity=None, name="Morningstar US Total Market PR USD", interval=86400):
    record = [
        identity or ["CRSPTM1", "INDEXNASDAQ"],
        "/g/1q52ggq8n",
        None,
        [[[], None, [BAR] if bars is None else bars]],
        None,
        -14400,
        5469.6895,
        name,
        interval,
        1,
    ]
    return (
        "<script>AF_initDataCallback({key: 'ds:changed', hash: '12', data:"
        + json.dumps([[record]])
        + ", sideChannel: {}});</script>"
    )


def test_daily_candle_closes_and_provenance_are_preserved_without_live_quote_fallback():
    row = parse_history(page(), SPEC, DAY, DAY, NOW, "google.json")[0]
    assert row.price == Decimal("5494.37")
    assert row.source == "google" and row.symbol == "US.CRSPTM1" and row.currency == "USD"
    assert row.trade_date == DAY and row.quote_at == datetime(2026, 10, 15, 20, tzinfo=UTC)
    assert row.raw["field"] == "daily_ohlc.close"
    assert row.raw["frequency_seconds"] == 86400
    assert row.artifact_path == "google.json" and row.eps_ttm is None
    assert json.loads(json.dumps(row.raw))["ohlc"][1] == "5494.37"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"identity": ["VTI", "NYSEARCA"]},
        {"identity": ["CRSPTMT", "INDEXNASDAQ"]},
        {"name": "Morningstar US Total Market TR USD"},
        {"interval": 60},
        {"interval": 604800},
        {"bars": []},
        {"bars": [[[2026, 10, 15], [5494.37]]]},
    ],
)
def test_wrong_identity_total_return_and_nondaily_charts_are_rejected(kwargs):
    with pytest.raises(DataError, match="one identifiable"):
        parse_history(page(**kwargs), SPEC, DAY, DAY, NOW, "bad")


def test_recent_window_filters_requested_dates_and_never_moves_a_quote_back_in_time():
    assert parse_history(page(), SPEC, date(2020, 1, 1), date(2020, 1, 10), NOW, "old") == []
    assert parse_history(page(), SPEC, DAY, DAY, NOW.replace(day=15, hour=19), "future") == []
    assert (
        parse_history(page(), SPEC, DAY, DAY, NOW.replace(day=15, hour=20, minute=10), "open") == []
    )


@pytest.mark.parametrize("value", [None, 0, -1, "NaN", "Infinity"])
def test_missing_or_invalid_daily_close_never_uses_open_high_or_low(value):
    bar = [*BAR]
    bar[1] = value
    with pytest.raises(DataError, match="closing price"):
        parse_history(page(bars=[bar]), SPEC, DAY, DAY, NOW, "bad")


@pytest.mark.parametrize(
    "stamp",
    ["invalid", "2026-10-15T16:00:00", "2026-10-15T16:00:00+08:00", "2026-10-15T15:30:00-04:00"],
)
def test_bad_timestamp_timezone_or_intraday_candle_is_rejected(stamp):
    bar = [*BAR]
    bar[4] = stamp
    with pytest.raises(DataError):
        parse_history(page(bars=[bar]), SPEC, DAY, DAY, NOW, "bad")


def test_duplicate_bars_and_ambiguous_pages_fail_closed():
    for text in (page(bars=[BAR, BAR]), page() + page(), "<html>consent</html>"):
        with pytest.raises(DataError):
            parse_history(text, SPEC, DAY, DAY, NOW, "bad")


def test_winter_offset_and_explicit_mapping_are_required():
    day = date(2026, 1, 5)
    bar = [*BAR]
    bar[4] = "2026-01-05T16:00:00-05:00"
    assert parse_history(page(bars=[bar]), SPEC, day, day, NOW, "winter")[0].trade_date == day
    with pytest.raises(DataError, match="mapping"):
        parse_history(page(), replace(SPEC, google_symbol="VTI:NYSEARCA"), DAY, DAY, NOW, "bad")


def test_live_adapter_archives_responses_and_rejects_http_failures(tmp_path, monkeypatch):
    def get(url, **kwargs):
        assert "CRSPTM1:INDEXNASDAQ?hl=en" in url
        return httpx.Response(200, text=page(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    result = GoogleHistory(Archive(tmp_path)).index_history(SPEC, DAY, DAY)
    assert result.observations[0].price == Decimal("5494.37")
    assert len(list(tmp_path.rglob("*.json"))) == 1
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kw: httpx.Response(
            429, text="rate limited", request=httpx.Request("GET", url)
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        GoogleHistory(Archive(tmp_path)).index_history(SPEC, DAY, DAY)
    assert any(json.loads(p.read_text())["status"] == 429 for p in tmp_path.rglob("*.json"))
