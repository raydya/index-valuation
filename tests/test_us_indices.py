import copy
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, Observation, ReferenceHistory
from index_valuation.history import HistoricalBackfill
from index_valuation.index_series import build_dashboard, derive_points
from index_valuation.providers.nasdaq_history import NasdaqHistory
from index_valuation.providers.nasdaq_history import parse_history as parse_nasdaq
from index_valuation.providers.us_calendar import USCalendar
from index_valuation.providers.yahoo_history import parse_history as parse_yahoo
from index_valuation.registry import load_indices

SPECS = load_indices()
SP = replace(
    SPECS["sp500"],
    yahoo_symbol="^GSPC",
    valuation_source="none",
    price_sources=("fred", "yahoo"),
)
DAY = date(2024, 7, 5)
NOW = datetime(2024, 7, 6, tzinfo=UTC)


def yahoo_payload():
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": "^GSPC",
                        "instrumentType": "INDEX",
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                        "dataGranularity": "1d",
                    },
                    "timestamp": [int(datetime(2024, 7, 5, 13, 30, tzinfo=UTC).timestamp())],
                    "indicators": {
                        "quote": [{"close": [5500]}],
                        "adjclose": [{"adjclose": [9999]}],
                    },
                }
            ],
        }
    }


def test_etf_benchmarks_are_index_series_in_native_currency():
    for key, etf in (("sp500", "VOO"), ("nasdaq100", "QQQ"), ("ustotal", "VTI")):
        spec = SPECS[key]
        assert spec.tracking_etfs == (etf,)
        assert spec.currency == "USD" and spec.market == "US"
        assert spec.timezone == "America/New_York" and not spec.futu_supported
        assert spec.quote_code != f"US.{etf}"
    assert SPECS["nasdaq100"].eastmoney_secid is None
    assert SPECS["ustotal"].yahoo_symbol == "^CRSPTM1"  # Price, not CRSPTMT total return.


@pytest.mark.parametrize(
    "change",
    [
        'currency="CNY"',
        'timezone="Asia/Shanghai"',
        'reference_history_source="csindex"',
        'yahoo_symbol="VOO"',
    ],
)
def test_registry_rejects_wrong_market_or_etf_substitution(tmp_path, change):
    config = """[indices.test]
name="Test"
provider_code="SPX"
quote_code="US..SPX"
currency="USD"
timezone="America/New_York"
reference_history_source="yahoo"
yahoo_symbol="^GSPC"
"""
    key = change.split("=")[0]
    config = "\n".join(line for line in config.splitlines() if not line.startswith(key + "="))
    path = tmp_path / "indices.toml"
    path.write_text(config + "\n" + change)
    with pytest.raises(DataError):
        load_indices(path)


def test_yahoo_uses_daily_close_not_adjusted_close_and_preserves_missing_price():
    payload = yahoo_payload()
    rows = parse_yahoo(payload, SP, DAY, DAY, NOW, "yahoo.json")
    assert rows[0].price == Decimal(5500)
    assert rows[0].symbol == SP.quote_code and rows[0].currency == "USD"
    assert rows[0].quote_at.utcoffset().total_seconds() == -4 * 3600
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [None]
    assert parse_yahoo(payload, SP, DAY, DAY, NOW, "missing.json") == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "^IXIC"),
        ("instrumentType", "ETF"),
        ("currency", "CNY"),
        ("dataGranularity", "3mo"),
        ("exchangeTimezoneName", "Asia/Shanghai"),
    ],
)
def test_yahoo_rejects_wrong_identity_and_silently_downsampled_data(field, value):
    payload = yahoo_payload()
    payload["chart"]["result"][0]["meta"][field] = value
    with pytest.raises(DataError):
        parse_yahoo(payload, SP, DAY, DAY, NOW, "bad.json")


def test_yahoo_rejects_unclosed_sessions_and_duplicate_dates():
    payload = yahoo_payload()
    with pytest.raises(DataError, match="unfinished"):
        parse_yahoo(payload, SP, DAY, DAY, datetime(2024, 7, 5, 19, tzinfo=UTC), "bad")
    data = payload["chart"]["result"][0]
    data["timestamp"] *= 2
    data["indicators"]["quote"][0]["close"] *= 2
    with pytest.raises(DataError, match="duplicate"):
        parse_yahoo(payload, SP, DAY, DAY, NOW, "bad")


def nasdaq_payload(rows, total=2):
    return {
        "status": {"rCode": 200},
        "data": {
            "symbol": "NDX",
            "totalRecords": total,
            "tradesTable": {"rows": rows},
        },
    }


def test_nasdaq_reads_all_pages_and_rejects_a_repeated_page(tmp_path, monkeypatch):
    client_type = httpx.Client
    offsets = []
    repeated = False

    def respond(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        day = "07/05/2024" if offset == 0 or repeated else "07/03/2024"
        return httpx.Response(200, json=nasdaq_payload([{"date": day, "close": "20,000.25"}]))

    monkeypatch.setattr(
        "index_valuation.providers.nasdaq_history.httpx.Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond)),
    )
    source = NasdaqHistory(Archive(tmp_path))
    rows = source.index_history(SPECS["nasdaq100"], date(2024, 7, 3), DAY).observations
    assert offsets == [0, 1] and len(rows) == 2
    assert rows[0].price == Decimal("20000.25")
    repeated = True
    with pytest.raises(DataError, match="pagination"):
        source.index_history(SPECS["nasdaq100"], date(2024, 7, 3), DAY)


def test_nasdaq_rejects_the_composite_index():
    payload = nasdaq_payload([{"date": "07/05/2024", "close": "18,000"}])
    bad = copy.deepcopy(payload)
    bad["data"]["symbol"] = "COMP"
    with pytest.raises(DataError, match="different index"):
        parse_nasdaq(bad, SPECS["nasdaq100"], DAY, DAY, NOW, "bad")


def test_us_calendar_special_closures_and_dst_are_archived(tmp_path):
    calendar = USCalendar(Archive(tmp_path))
    sessions, _ = calendar.trading_days(date(2001, 9, 10), date(2001, 9, 17), market="US")
    assert sessions == {date(2001, 9, 10), date(2001, 9, 17)}
    sessions, _ = calendar.trading_days(date(2012, 10, 29), date(2012, 10, 31), market="US")
    assert sessions == {date(2012, 10, 31)}
    sessions, _ = calendar.trading_days(date(2025, 1, 9), date(2025, 1, 9), market="US")
    assert not sessions
    _, path = calendar.trading_days(date(2026, 3, 6), date(2026, 3, 9), market="US")
    payload = json.loads(open(path).read())
    assert payload["rows"][0]["close"] == "2026-03-06T21:00:00+00:00"
    assert payload["rows"][1]["close"] == "2026-03-09T20:00:00+00:00"
    assert payload["version"]


def test_us_price_only_pipeline_does_not_need_opend_and_keeps_pe_missing(tmp_path):
    row = Observation(
        SP.quote_code, DAY, "yahoo", Decimal(5500), None, None, NOW, NOW, currency="USD"
    )
    calendar = SimpleNamespace(
        source_name="test_calendar", trading_days=lambda *a, **kw: ({DAY}, "calendar.json")
    )
    reference = SimpleNamespace(
        source_name="yahoo", index_history=lambda *_: ReferenceHistory([row])
    )
    saved = []
    repo = SimpleNamespace(
        save_observations=lambda _, rows: saved.extend(rows),
        save_source_valuations=lambda *_: None,
        weight_dates=lambda _: [],
    )
    report = {"sources": {}, "quarantined_rows": []}
    HistoricalBackfill(repo, None, None, reference, tmp_path, calendar_source=calendar)._collect(
        SP, DAY, DAY, "run", report
    )
    assert saved == [row] and report["trading_calendar"]["market"] == "US"
    assert report["sources"]["futu_pe"]["status"] == "unsupported"
    points = derive_points(SP, [row, replace(row, source="fred")], [])
    assert points[0]["price_status"] == "pass"
    assert points[0]["pe"] is None and points[0]["implied_eps"] is None
    dashboard = build_dashboard(SP, points)
    assert dashboard["index"]["tracking_etfs"] == ("VOO",)
    assert dashboard["selected"]["fixed"] == {}
    assert dashboard["selected"]["mean_pe"] is None
