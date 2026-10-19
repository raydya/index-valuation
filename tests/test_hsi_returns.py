"""Only the correctly identified gross return series supplies reinvestment returns."""

import copy
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError
from index_valuation.providers.hsi_returns import HSITotalReturnHistory, parse_history
from index_valuation.registry import load_indices

SPEC = replace(load_indices()["hstech"], provider_code="12083.00", quote_code="HK.HSTECHT")
NOW = datetime(2026, 10, 19, 4, tzinfo=UTC)
START, END = date(2026, 10, 15), date(2026, 10, 16)
PAYLOAD = {
    "code": "0",
    "data": [
        {
            "indexCode": "12083.00",
            "indexName": "Hang Seng TECH Index (Gross Total Return Index)",
            "valueHistory": [
                {"date": "2021-10-15", "close": "6816.56"},
                {"date": "2026-10-15", "close": "4573.55"},
                {"date": "2026-10-16", "close": "4626.76"},
            ],
        }
    ],
}


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def client_mock(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr("index_valuation.providers.hsi_returns.datetime", FixedClock)
    monkeypatch.setattr(
        "index_valuation.providers.hsi_returns.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler)),
    )


def test_rolling_history_is_filtered_to_requested_dates_and_keeps_gross_identity():
    result = parse_history(PAYLOAD, SPEC, START, END, NOW, "raw.json")
    assert not result.failures
    assert [x.trade_date for x in result.observations] == [START, END]
    row = result.observations[-1]
    assert row.price == Decimal("4626.76") and row.currency == "HKD"
    assert row.price_basis == "gross_total_return" and row.symbol == "HK.HSTECHT"
    assert row.pe_ttm is None and row.eps_ttm is None
    assert row.quote_at == datetime(2026, 10, 16, 8, 10, tzinfo=UTC)
    assert row.raw["indexCode"] == "12083.00"


def test_older_requested_history_is_explicitly_unavailable():
    result = parse_history(PAYLOAD, SPEC, date(2014, 12, 31), END, NOW, "raw.json")
    assert len(result.observations) == 3
    gap = result.failures[0]
    assert gap["category"] == "unsupported_historical_coverage"
    assert gap["start"] == date(2014, 12, 31) and gap["end"] == date(2021, 10, 14)
    assert gap["available_end"] == END


@pytest.mark.parametrize(
    "change",
    [
        {"indexCode": "02083.00"},
        {"indexCode": "22083.00"},
        {"indexName": "Hang Seng TECH Index"},
        {"indexName": "Hang Seng TECH Index (Net Total Return Index)"},
    ],
)
def test_rejects_price_net_and_mismatched_identities(change):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0].update(change)
    with pytest.raises(DataError, match="different total-return index"):
        parse_history(payload, SPEC, START, END, NOW, "raw.json")


@pytest.mark.parametrize(
    "rows,match",
    [
        ([], "no total-return history"),
        ([{"date": "invalid", "close": "100"}], "invalid total-return date"),
        ([{"date": "2026-10-16T00:00:00", "close": "100"}], "invalid total-return date"),
        ([{"date": "2026-10-16", "close": "0"}], "invalid total-return close"),
        ([{"date": "2026-10-16", "close": "-1"}], "invalid total-return close"),
        ([{"date": "2026-10-16", "close": "NaN"}], "invalid total-return close"),
        ([{"date": "2026-10-16", "close": None}], "invalid total-return close"),
        ([{"date": "2026-10-16", "close": "100"}] * 2, "duplicate"),
    ],
)
def test_invalid_rows_are_not_silently_accepted(rows, match):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0]["valueHistory"] = rows
    with pytest.raises(DataError, match=match):
        parse_history(payload, SPEC, START, END, NOW, "raw.json")


def test_unclosed_session_is_rejected():
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0]["valueHistory"] = [{"date": "2026-10-19", "close": "100"}]
    with pytest.raises(DataError, match="unfinished"):
        parse_history(payload, SPEC, START, date(2026, 10, 19), NOW, "raw.json")


def test_fetches_rolling_window_once_and_retains_http_evidence(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=PAYLOAD)

    client_mock(monkeypatch, handler)
    result = HSITotalReturnHistory(Archive(tmp_path)).index_history(SPEC, date(2014, 12, 31), END)
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {
        "data": "valueHistoryPub",
        "indexCodes": "12083.00",
        "language": "en-hk",
    }
    raw = json.loads(Path(result.observations[-1].artifact_path).read_text())
    assert raw["http_status"] == 200 and json.loads(raw["text"]) == PAYLOAD


def test_wrong_mapping_is_rejected_before_request(tmp_path):
    with pytest.raises(DataError, match="explicitly mapped"):
        HSITotalReturnHistory(Archive(tmp_path)).index_history(
            replace(SPEC, provider_code="22083.00"), START, END
        )


def test_network_and_http_failures_are_reported_without_fake_data(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(503, text="Unavailable")

    client_mock(monkeypatch, handler)
    result = HSITotalReturnHistory(Archive(tmp_path)).index_history(SPEC, START, END)
    assert not result.observations
    assert result.failures[0]["category"] == "source_failure"
    assert Path(result.failures[0]["artifact_path"]).exists()
