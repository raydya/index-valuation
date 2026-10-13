import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, IndexSpec, ReferenceHistory, SourceError
from index_valuation.history import admit_rows, date_gaps
from index_valuation.providers.baostock import BaoStockSource
from index_valuation.providers.csindex_history import parse_history_row
from index_valuation.providers.futu import FutuSource, parse_history_bar, parse_valuation_history

SPEC = IndexSpec("test", "Test", "000300", "SH.000300", 300)
DAY = date(2005, 4, 8)
MONDAY = date(2005, 4, 11)
OBSERVED_AT = datetime(2026, 10, 13, tzinfo=UTC)


def bar(day=DAY):
    return {"code": SPEC.quote_code, "time_key": f"{day} 00:00:00", "close": 1000, "pe_ratio": 15}


def test_kline_pe_without_ttm_definition_cannot_become_eps():
    result = parse_history_bar(bar(), SPEC, OBSERVED_AT, "raw.json")
    assert result.price == 1000
    assert result.eps_ttm is result.pe_ttm is None
    assert result.raw["pe_ratio"] == 15
    assert result.price_basis == "unadjusted_close"


def test_vendor_pe_is_separate_and_filters_prelaunch_and_current_rows():
    data = {
        "valuation_type": "PE",
        "trend": {
            "historical_items": [
                {"time_str": "2005-01-04", "value": 10},
                {"time_str": "2005-04-08", "value": 15},
                {"time_str": "2026-10-13", "value": 20},
            ]
        },
    }
    rows = parse_valuation_history(data, SPEC, DAY, MONDAY, OBSERVED_AT, "raw.json")
    assert len(rows) == 1
    assert rows[0].value == Decimal(15)
    assert rows[0].metric == "pe"
    assert rows[0].basis == "futu_valuation_pe"


def test_official_null_rolling_pe_remains_missing():
    price, pe = parse_history_row(
        {"indexCode": "000300", "tradeDate": "20050408", "close": 1003.45, "peg": None},
        SPEC,
        OBSERVED_AT,
        "raw.json",
    )
    assert price.price == Decimal("1003.45")
    assert pe is None


def test_gaps_count_trading_sessions_and_off_calendar_rows_are_quarantined():
    sessions = {DAY, MONDAY}
    assert date_gaps(sessions, set()) == [{"start": DAY, "end": MONDAY, "sessions": 2}]
    friday = parse_history_bar(bar(), SPEC, OBSERVED_AT, "raw.json")
    sunday = replace(friday, trade_date=date(2005, 4, 10))
    rows, quarantine = admit_rows([friday, sunday], SPEC, DAY, MONDAY, sessions)
    assert rows == [friday]
    assert quarantine[0]["date"] == sunday.trade_date
    assert quarantine[0]["artifact_path"] == "raw.json"
    with pytest.raises(DataError, match="duplicate"):
        admit_rows([friday, friday], SPEC, DAY, MONDAY, sessions)


def test_futu_history_paginates_and_explicitly_uses_unadjusted_prices(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "futu",
        SimpleNamespace(
            AuType=SimpleNamespace(NONE="NONE"),
            KLType=SimpleNamespace(K_DAY="K_DAY"),
        ),
    )
    monkeypatch.setattr("index_valuation.providers.futu.time.sleep", lambda _: None)
    calls = []

    def request(code, **kwargs):
        calls.append(kwargs)
        day, key = (DAY, b"next") if len(calls) == 1 else (MONDAY, None)
        return 0, SimpleNamespace(to_json=lambda **_: json.dumps([bar(day)])), key

    source = FutuSource(Archive(tmp_path), "localhost", 11111)
    source.ctx = SimpleNamespace(request_history_kline=request)
    rows = source.index_history(SPEC, DAY, MONDAY)
    assert [row.trade_date for row in rows] == [DAY, MONDAY]
    assert all(call["autype"] == "NONE" for call in calls)
    assert calls[1]["page_req_key"] == b"next"


def test_current_day_history_is_rejected_before_accessing_sources(tmp_path):
    from index_valuation.history import HistoricalBackfill

    job = HistoricalBackfill(None, None, None, None, tmp_path)
    with pytest.raises(DataError, match="end before today"):
        job.run(SPEC, DAY, date(2099, 1, 1))


def calendar_archive(archive, observed_at=OBSERVED_AT, rows=None):
    return archive.save_json(
        "baostock_history",
        {
            "observed_at": observed_at,
            "request": {"endpoint": "query_trade_dates", "start": DAY, "end": MONDAY},
            "rows": rows
            if rows is not None
            else [
                {"calendar_date": "2005-04-08", "is_trading_day": "1"},
                {"calendar_date": "2005-04-09", "is_trading_day": "0"},
                {"calendar_date": "2005-04-10", "is_trading_day": "0"},
                {"calendar_date": "2005-04-11", "is_trading_day": "1"},
            ],
        },
    )


def test_calendar_fallback_keeps_holidays_and_requires_complete_historical_coverage(tmp_path):
    archive = Archive(tmp_path)
    source = BaoStockSource(archive)
    path = calendar_archive(archive)
    assert source.cached_trading_days(DAY, MONDAY) == ({DAY, MONDAY}, path)
    assert source.cached_trading_days(date(2005, 4, 9), date(2005, 4, 10))[0] == set()
    with pytest.raises(SourceError, match="covers"):
        source.cached_trading_days(DAY, date(2005, 4, 12))


@pytest.mark.parametrize(
    "problem", ["missing_day", "duplicate", "invalid_flag", "future", "corrupt"]
)
def test_untrustworthy_calendar_cache_is_rejected(tmp_path, problem):
    from pathlib import Path

    archive = Archive(tmp_path)
    path = Path(calendar_archive(archive))
    payload = json.loads(path.read_text())
    path.unlink()
    if problem == "missing_day":
        payload["rows"].pop(1)
    elif problem == "duplicate":
        payload["rows"].append(payload["rows"][0])
    elif problem == "invalid_flag":
        payload["rows"][0]["is_trading_day"] = "unknown"
    elif problem == "future":
        payload["observed_at"] = "2005-04-08T15:30:00+08:00"
    path = Path(archive.save_json("baostock_history", payload))
    if problem == "corrupt":
        path.write_text(path.read_text() + "\n")
    with pytest.raises(SourceError, match="covers"):
        BaoStockSource(archive).cached_trading_days(DAY, MONDAY)


@pytest.mark.parametrize("cached", [True, False])
def test_secondary_outage_uses_calendar_fallback_and_keeps_other_sources(tmp_path, cached):
    from index_valuation.history import HistoricalBackfill

    archive = Archive(tmp_path / "raw")
    calendar_path = calendar_archive(archive) if cached else "futu-calendar.json"

    class OfflineBaoStock(BaoStockSource):
        def __enter__(self):
            raise SourceError("temporarily unavailable")

    class Primary:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def index_history(self, *_):
            return [parse_history_bar(bar(), SPEC, OBSERVED_AT, "futu.json")]

        def valuation_history(self, *_):
            return []

        def trading_days(self, *_):
            return {DAY, MONDAY}, "futu-calendar.json"

    saved = []
    repository = SimpleNamespace(
        save_observations=lambda _, rows: saved.extend(rows),
        save_source_valuations=lambda *_: None,
        weight_dates=lambda _: [],
    )
    official = SimpleNamespace(index_history=lambda *_: ReferenceHistory())
    job = HistoricalBackfill(
        repository, Primary, lambda: OfflineBaoStock(archive), official, tmp_path
    )
    report = {"sources": {}, "quarantined_rows": []}
    job._collect(SPEC, DAY, MONDAY, "test-run", report)
    assert report["trading_calendar"]["cached"] is cached
    assert report["trading_calendar"]["source"] == ("baostock" if cached else "futu")
    assert report["trading_calendar"]["artifact_path"] == calendar_path
    assert report["sources"]["baostock_price"]["status"] == "failed"
    assert report["sources"]["futu_price"]["rows"] == 1
    assert len(saved) == 1 and saved[0].source == "futu"


def test_futu_calendar_fallback_validates_dates_and_rejects_uncovered_history(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(
        sys.modules, "futu", SimpleNamespace(TradeDateMarket=SimpleNamespace(CN="CN"))
    )
    source = FutuSource(Archive(tmp_path), "localhost", 11111)
    rows = [{"time": "2026-10-12", "trade_date_type": "WHOLE"}]
    source.ctx = SimpleNamespace(request_trading_days=lambda **_: (0, rows))
    sessions, path = source.trading_days(date(2026, 10, 10), date(2026, 10, 12))
    assert sessions == {date(2026, 10, 12)}
    assert "futu_calendar" in path
    rows.append(rows[0])
    with pytest.raises(DataError, match="calendar dates"):
        source.trading_days(date(2026, 10, 10), date(2026, 10, 12))
    with pytest.raises(SourceError, match="ten years"):
        source.trading_days(DAY, MONDAY)


def test_official_history_keeps_available_years_and_reports_failed_ranges(tmp_path, monkeypatch):
    import httpx

    from index_valuation.providers.csindex_history import CSIHistory

    def respond(request):
        if request.url.params["startDate"] == "20191231":
            return httpx.Response(403)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": [
                    {
                        "indexCode": SPEC.provider_code,
                        "tradeDate": "20200102",
                        "close": 1000,
                        "peg": 20,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(respond))
    batch = CSIHistory(Archive(tmp_path)).index_history(SPEC, date(2019, 12, 31), date(2020, 1, 2))
    assert len(batch.observations) == len(batch.valuations) == 1
    assert batch.observations[0].trade_date == date(2020, 1, 2)
    assert batch.failures[0]["start"] == date(2019, 12, 31)


def test_official_history_stops_after_repeated_failures(tmp_path, monkeypatch):
    import httpx

    from index_valuation.providers.csindex_history import CSIHistory

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503)

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(respond))
    batch = CSIHistory(Archive(tmp_path)).index_history(SPEC, date(2010, 1, 1), date(2020, 1, 1))
    assert len(calls) == 3
    assert not batch.observations
    assert batch.failures[-1]["end"] == date(2020, 1, 1)
