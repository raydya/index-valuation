"""Gross total returns retain index identity, true session dates and daily granularity."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError
from index_valuation.providers.nasdaq_returns import NasdaqTotalReturnHistory, parse_history
from index_valuation.registry import load_indices

SPEC = replace(load_indices()["nasdaq100"], quote_code="US.XNDX", nasdaq_symbol="XNDX")
NOW = datetime(2026, 10, 19, 4, tzinfo=UTC)
START = date(2026, 10, 15)
END = date(2026, 10, 16)


def bar(day="2026-10-16", **changes):
    stamp = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    return {"x": stamp, "y": Decimal("35675.89765529041"), "FPSymbol": "XNDX", **changes}


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def client_mock(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr("index_valuation.providers.nasdaq_returns.datetime", FixedClock)
    monkeypatch.setattr(
        "index_valuation.providers.nasdaq_returns.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler)),
    )


def test_daily_date_and_dividend_basis_are_separate_from_price_valuation():
    rows = parse_history([bar()], SPEC, START, END, NOW, "evidence.json")
    assert rows[0].trade_date == END  # UTC midnight is NOT October 15 in New York.
    assert rows[0].price == Decimal("35675.89765529041")
    assert rows[0].quote_at == datetime(2026, 10, 16, 20, 15, tzinfo=UTC)
    assert rows[0].symbol == "US.XNDX"
    assert rows[0].price_basis == "gross_total_return"
    assert rows[0].eps_ttm is None and rows[0].pe_ttm is None


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"data": []}, "history list"),
        ([bar(FPSymbol="NDX")], "different total-return index"),
        ([bar(FPSymbol="XNDXNNR")], "different total-return index"),
        ([bar(FPSymbol=None)], "different total-return index"),
        ([bar(x=1792108800001)], "UTC session date"),
        ([bar(x="1792108800000")], "UTC session date"),
        ([bar(x=True)], "UTC session date"),
        ([bar(x=86_400_000 * 10**15)], "invalid total-return date"),
        ([bar("2026-10-14")], "out-of-range"),
        ([bar(), bar()], "duplicate"),
        ([bar(y=0)], "invalid or unfinished"),
        ([bar(y=-1)], "invalid or unfinished"),
        ([bar(y="NaN")], "invalid or unfinished"),
        ([bar(y=None)], "invalid or unfinished"),
    ],
)
def test_rejects_wrong_identity_dates_and_invalid_values(payload, match):
    with pytest.raises(DataError, match=match):
        parse_history(payload, SPEC, START, END, NOW, "raw.json")


def test_unclosed_us_session_cannot_be_ingested():
    with pytest.raises(DataError, match="unfinished"):
        parse_history(
            [bar("2026-10-19")], SPEC, date(2026, 10, 19), date(2026, 10, 19), NOW, "raw.json"
        )


@pytest.mark.parametrize(
    "changes", [{"nasdaq_symbol": "NDX"}, {"quote_code": "US..NDX"}, {"currency": "HKD"}]
)
def test_rejects_price_index_or_wrong_currency_mapping(tmp_path, changes):
    with pytest.raises(DataError, match="explicitly mapped"):
        NasdaqTotalReturnHistory(Archive(tmp_path)).index_history(
            replace(SPEC, **changes), START, END
        )


def test_bounds_requests_to_one_year_and_archives_original_precision(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        params = parse_qs(request.content.decode())
        calls.append(params)
        year = params["startDate"][0][:4]
        day = {"2024": "2024-12-31", "2025": "2025-12-31", "2026": "2026-01-02"}[year]
        body = json.dumps([bar(day, y="35675.89765529041")])
        return httpx.Response(200, text=body.replace('"35675.89765529041"', "35675.89765529041"))

    client_mock(monkeypatch, handler)
    batch = NasdaqTotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(2024, 12, 31), date(2026, 1, 2)
    )
    assert not batch.failures
    assert [(x["startDate"][0], x["endDate"][0]) for x in calls] == [
        ("2024-12-31", "2024-12-31"),
        ("2025-01-01", "2025-12-31"),
        ("2026-01-01", "2026-01-02"),
    ]
    assert all(x["id"] == ["XNDX"] for x in calls)
    for row in batch.observations:
        assert row.price == Decimal("35675.89765529041")
        evidence = json.loads(Path(row.artifact_path).read_text())
        assert "35675.89765529041" in evidence["text"]
        assert evidence["http_status"] == 200


def test_window_failure_keeps_other_years_and_does_not_accept_unbounded_chart(
    tmp_path, monkeypatch
):
    def handler(request):
        params = parse_qs(request.content.decode())
        if params["startDate"] == ["2025-01-01"]:
            return httpx.Response(200, json=[bar("2024-12-31", y=100)])
        return httpx.Response(200, json=[bar("2026-01-02", y=110)])

    client_mock(monkeypatch, handler)
    batch = NasdaqTotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(2025, 1, 1), date(2026, 1, 2)
    )
    assert len(batch.failures) == 1
    assert batch.failures[0]["category"] == "invalid_data"
    assert "out-of-range" in batch.failures[0]["error"]
    assert [x.trade_date for x in batch.observations] == [date(2026, 1, 2)]


def test_pre_launch_request_does_not_relabel_current_history(tmp_path, monkeypatch):
    def handler(request):
        raise AssertionError("No request should be made before available XNDX history")

    client_mock(monkeypatch, handler)
    batch = NasdaqTotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(1985, 1, 1), date(1999, 3, 3)
    )
    assert batch.observations == []
    assert batch.failures[0]["category"] == "unsupported_historical_coverage"


def test_response_cannot_exceed_daily_window():
    with pytest.raises(DataError, match="bounded daily window"):
        parse_history([bar(), bar()], SPEC, END, END, NOW, "raw.json")


def test_numeric_response_raw_is_jsonb_serializable_without_losing_precision():
    # This matches the real HTTP decoder: non-integer numbers become Decimal.
    payload = json.loads(
        '[{"x":1792108800000,"y":35675.89765529041,"FPSymbol":"XNDX",'
        '"Open":36590.23322685168,"High":36452.50333889029,"Low":null,'
        '"Change":-2.4999999999999999}]',
        parse_float=Decimal,
    )
    row = parse_history(payload, SPEC, START, END, NOW, "original-response.json")[0]
    # Use the same plain encoder used by SQLAlchemy's JSONB binding.
    restored = json.loads(json.dumps(row.raw, allow_nan=False))
    assert row.price == Decimal("35675.89765529041")
    assert restored["y"] == "35675.89765529041"
    assert restored["Open"] == "36590.23322685168"
    assert restored["High"] == "36452.50333889029"
    assert restored["Change"] == "-2.4999999999999999"
    assert restored["x"] == 1792108800000 and restored["Low"] is None
    assert payload[0]["y"] == Decimal("35675.89765529041")


def test_zero_and_negative_rows_are_isolated_without_losing_valid_years(tmp_path, monkeypatch):
    def handler(request):
        params = parse_qs(request.content.decode())
        if params["startDate"][0].startswith("2025"):
            return httpx.Response(200, json=[bar("2025-12-31", y=100)])
        return httpx.Response(
            200,
            json=[
                bar("2026-10-12", y=0),
                bar("2026-10-13", y=102),
                bar("2026-10-14", y=-1),
                bar("2026-10-16", y=104),
            ],
        )

    client_mock(monkeypatch, handler)
    result = NasdaqTotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(2025, 12, 31), END
    )
    assert [(x.trade_date, x.price) for x in result.observations] == [
        (date(2025, 12, 31), Decimal(100)),
        (date(2026, 10, 13), Decimal(102)),
        (END, Decimal(104)),
    ]
    assert [x["trade_date"] for x in result.failures] == [date(2026, 10, 12), date(2026, 10, 14)]
    for failure in result.failures:
        assert failure["category"] == "invalid_observation"
        assert failure["start"] == failure["end"] == failure["trade_date"]
        evidence = json.loads(Path(failure["artifact_path"]).read_text())
        assert evidence["parameters"]["id"] == "XNDX"
        assert failure["value"] in {"0", "-1"}


def test_wrong_identity_still_rejects_entire_window_after_an_invalid_value(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json=[
                bar("2026-10-15", y=0),
                bar("2026-10-16", y=100, FPSymbol="NDX"),
            ],
        )

    client_mock(monkeypatch, handler)
    result = NasdaqTotalReturnHistory(Archive(tmp_path)).index_history(SPEC, START, END)
    assert result.observations == []
    assert len(result.failures) == 1
    assert result.failures[0]["category"] == "invalid_data"
    assert "different total-return index" in result.failures[0]["error"]
