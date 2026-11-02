"""Official HSI price closes must remain separate from total-return indices."""

import copy
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, SourceValuation
from index_valuation.index_series import derive_points
from index_valuation.providers.hsi_history import HSIPriceHistory, parse_history
from index_valuation.registry import load_indices

NOW = datetime(2026, 11, 2, 2, 55, tzinfo=UTC)
START, END = date(2026, 10, 23), date(2026, 10, 30)
PAYLOAD = {
    "code": "0",
    "data": [
        {
            "indexCode": "02083.00",
            "indexName": "Hang Seng TECH Index",
            "valueHistory": [
                {"date": "2021-10-29", "close": "6375.85"},
                {"date": "2026-10-23", "close": "3871.66"},
                {"date": "2026-10-30", "close": "3754.90"},
            ],
        }
    ],
}
REGISTRY_CONFIG = """[indices.test]
name="Test"
provider_code="HSTECH"
quote_code="HK.800700"
currency="HKD"
timezone="Asia/Hong_Kong"
close_time="16:10"
reference_history_source="hsi"
hsi_symbol="02083.00"
eastmoney_secid="124.HSTECH"
"""


@pytest.fixture
def spec():
    return replace(load_indices()["hstech"], hsi_symbol="02083.00")


def test_configured_hstech_uses_official_reference_without_requesting_eastmoney():
    configured = load_indices()["hstech"]
    assert configured.hsi_symbol == "02083.00"
    assert configured.reference_history_source == "hsi"
    assert configured.eastmoney_secid is None
    assert configured.historical_price_source == configured.valuation_source == "futu"
    assert configured.total_return.symbol == "12083.00"


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def mock_client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr("index_valuation.providers.hsi_history_base.datetime", FixedClock)
    monkeypatch.setattr(
        "index_valuation.providers.hsi_history_base.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler)),
    )


def test_official_price_history_keeps_close_identity_and_decimal_precision(spec):
    result = parse_history(PAYLOAD, spec, START, END, NOW, "prices.json")
    assert not result.failures
    assert [row.trade_date for row in result.observations] == [START, END]
    row = result.observations[-1]
    assert row.price == Decimal("3754.90")
    assert row.source == "hsi" and row.symbol == "HK.800700" and row.currency == "HKD"
    assert row.price_basis == "unadjusted_close"
    assert row.pe_ttm is None and row.eps_ttm is None
    assert row.quote_at == datetime(2026, 10, 30, 8, 10, tzinfo=UTC)
    assert row.observed_at == NOW and row.artifact_path == "prices.json"
    assert row.raw["indexCode"] == "02083.00"
    assert row.raw["indexName"] == "Hang Seng TECH Index"


def test_requested_history_before_rolling_window_reports_actual_coverage(spec):
    result = parse_history(PAYLOAD, spec, date(2014, 12, 31), END, NOW, "prices.json")
    assert len(result.observations) == 3
    gap = result.failures[0]
    assert gap["stage"] == "prices"
    assert gap["category"] == "unsupported_historical_coverage"
    assert gap["start"] == date(2014, 12, 31)
    assert gap["end"] == date(2021, 10, 28)
    assert gap["available_start"] == date(2021, 10, 29)
    assert gap["available_end"] == END


@pytest.mark.parametrize(
    "reference_multiplier,reference_day,expected_status,usable",
    [
        ("1", END, "pass", True),
        ("1.02", END, "warning", True),
        ("1.06", END, "error", False),
        ("1", START, "unverified", False),
    ],
)
def test_hsi_reference_preserves_futu_prices_pe_and_same_day_qualification(
    spec, reference_multiplier, reference_day, expected_status, usable
):
    official = parse_history(PAYLOAD, spec, END, END, NOW, "official.json").observations[0]
    primary = replace(official, source="futu", artifact_path="futu.json")
    reference = replace(
        official,
        price=official.price * Decimal(reference_multiplier),
        trade_date=reference_day,
    )
    pe = SourceValuation(
        "futu", spec.quote_code, END, "pe", "futu_valuation_pe", Decimal("19.995"), NOW, "pe.json"
    )
    point = next(
        row for row in derive_points(spec, [primary, reference], [pe]) if row["trade_date"] == END
    )
    assert point["price_source"] == point["pe_source"] == "futu"
    assert point["price"] == Decimal("3754.90") and point["pe"] == Decimal("19.995")
    assert point["price_status"] == expected_status
    assert point["price_usable"] is usable
    assert point["implied_eps"] == (primary.price / pe.value if usable else None)


def test_registry_accepts_verified_official_price_mapping(tmp_path):
    path = tmp_path / "indices.toml"
    path.write_text(REGISTRY_CONFIG)
    spec = load_indices(path)["test"]
    assert spec.hsi_symbol == "02083.00" and spec.reference_history_source == "hsi"
    assert spec.valuation_source == "futu"


@pytest.mark.parametrize(
    "change",
    [
        {"indexCode": "12083.00"},
        {"indexCode": "22083.00"},
        {"indexCode": "02000.00"},
        {"indexName": "Hang Seng Index"},
        {"indexName": "Hang Seng TECH Index (Gross Total Return Index)"},
        {"indexName": "Hang Seng TECH Index (Net Total Return Index)"},
    ],
)
def test_rejects_other_index_and_total_return_payloads(spec, change):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0].update(change)
    with pytest.raises(DataError):
        parse_history(payload, spec, START, END, NOW, "bad.json")


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [None],
        [{"date": "invalid", "close": "100"}],
        [{"date": "2026-10-30T00:00:00", "close": "100"}],
        [{"date": None, "close": "100"}],
        [{"date": "2026-10-30", "close": "0"}],
        [{"date": "2026-10-30", "close": "-1"}],
        [{"date": "2026-10-30", "close": "NaN"}],
        [{"date": "2026-10-30", "close": "Infinity"}],
        [{"date": "2026-10-30", "close": None}],
        [{"date": "2026-10-30", "close": "100"}] * 2,
    ],
)
def test_invalid_price_rows_are_rejected_without_partial_fake_results(spec, rows):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0]["valueHistory"] = rows
    with pytest.raises(DataError):
        parse_history(payload, spec, START, END, NOW, "bad.json")


def test_duplicate_dates_outside_requested_window_still_invalidate_payload(spec):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0]["valueHistory"].append(PAYLOAD["data"][0]["valueHistory"][0])
    with pytest.raises(DataError):
        parse_history(payload, spec, START, END, NOW, "bad.json")


def test_unfinished_hong_kong_session_is_not_treated_as_a_close(spec):
    payload = copy.deepcopy(PAYLOAD)
    payload["data"][0]["valueHistory"] = [{"date": "2026-11-02", "close": "4000"}]
    with pytest.raises(DataError):
        parse_history(payload, spec, START, date(2026, 11, 2), NOW, "bad.json")


@pytest.mark.parametrize("payload", [{}, {"code": 1}, {"code": 0, "data": []}])
def test_invalid_success_or_identity_envelope_is_rejected(spec, payload):
    with pytest.raises(DataError):
        parse_history(payload, spec, START, END, NOW, "bad.json")


def test_fetch_uses_price_code_once_and_archives_raw_http_evidence(spec, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=PAYLOAD)

    mock_client(monkeypatch, handler)
    result = HSIPriceHistory(Archive(tmp_path)).index_history(spec, START, END)
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {
        "data": "valueHistoryPub",
        "indexCodes": "02083.00",
        "language": "en-hk",
    }
    assert len(result.observations) == 2 and not result.failures
    raw = json.loads(Path(result.observations[-1].artifact_path).read_text())
    assert raw["http_status"] == 200 and json.loads(raw["text"]) == PAYLOAD


@pytest.mark.parametrize(
    "changes",
    [
        {"hsi_symbol": "12083.00"},
        {"hsi_symbol": "22083.00"},
        {"hsi_symbol": None},
        {"provider_code": "HSI"},
        {"quote_code": "HK.003033"},
        {"currency": "CNY"},
        {"timezone": "Asia/Shanghai"},
        {"quote_code": "SH.000300", "currency": "CNY", "timezone": "Asia/Shanghai"},
    ],
)
def test_wrong_mapping_is_rejected_before_any_request(spec, tmp_path, monkeypatch, changes):
    def handler(_):
        pytest.fail("Invalid HSI price identity must not make a request")

    mock_client(monkeypatch, handler)
    with pytest.raises(DataError):
        HSIPriceHistory(Archive(tmp_path)).index_history(replace(spec, **changes), START, END)


@pytest.mark.parametrize("failure", ["http", "network", "invalid_json", "wrong_identity"])
def test_source_failures_have_no_synthetic_prices(spec, tmp_path, monkeypatch, failure):
    def handler(request):
        if failure == "network":
            raise httpx.ConnectError("Unavailable", request=request)
        if failure == "http":
            return httpx.Response(503, text="Unavailable")
        if failure == "invalid_json":
            return httpx.Response(200, text="not JSON")
        payload = copy.deepcopy(PAYLOAD)
        payload["data"][0]["indexCode"] = "12083.00"
        return httpx.Response(200, json=payload)

    mock_client(monkeypatch, handler)
    result = HSIPriceHistory(Archive(tmp_path)).index_history(spec, START, END)
    assert not result.observations
    assert result.failures[0]["stage"] == "prices"
    assert result.failures[0]["category"] == (
        "source_failure" if failure in {"http", "network"} else "invalid_data"
    )
    if failure != "network":
        assert Path(result.failures[0]["artifact_path"]).exists()


@pytest.mark.parametrize(
    "override",
    [
        'hsi_symbol="12083.00"',
        'hsi_symbol="22083.00"',
        'hsi_symbol=""',
        'provider_code="HSI"',
        'quote_code="HK.003033"',
        'quote_code="SH.000300"',
        'currency="CNY"',
        'timezone="Asia/Shanghai"',
    ],
)
def test_registry_rejects_unverified_hsi_price_identity(tmp_path, override):
    key = override.split("=")[0]
    lines = [line for line in REGISTRY_CONFIG.splitlines() if not line.startswith(key + "=")]
    path = tmp_path / "indices.toml"
    path.write_text("\n".join([*lines, override]))
    with pytest.raises(DataError):
        load_indices(path)
