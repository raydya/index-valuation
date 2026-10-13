import csv
import json
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, IndexSpec, Observation, SourceValuation
from index_valuation.history import HistoricalBackfill
from index_valuation.index_series import build_dashboard, derive_points, series_csv
from index_valuation.providers.cnindex_history import parse_history
from index_valuation.providers.eastmoney import parse_prices, security_id
from index_valuation.providers.futu import FutuSource
from index_valuation.registry import load_indices

HK = IndexSpec(
    "hk",
    "HK Test",
    "HSTECH",
    "HK.800700",
    currency="HKD",
    timezone="Asia/Hong_Kong",
    close_time="16:10",
    reference_history_source="none",
    eastmoney_secid="124.HSTECH",
    history_start_date=date(2014, 12, 31),
)
DAY = date(2026, 10, 6)  # HK trades during the mainland National Day closure.
OBSERVED = datetime(2026, 10, 13, 10, tzinfo=UTC)


def test_registry_supports_separate_markets_and_variable_constituent_counts():
    specs = load_indices()
    assert len(specs) == 6
    assert specs["sse"].expected_constituents is None
    assert specs["hstech"].market == "HK"
    assert specs["hstech"].currency == "HKD"
    assert specs["chinext"].reference_history_source == "cnindex"
    assert specs["star50"].inception_date == date(2020, 7, 23)


@pytest.mark.parametrize("change", ['currency="CNY"', 'reference_history_source="csindex"'])
def test_registry_rejects_cross_market_configuration(tmp_path, change):
    text = """[indices.hk]
name="HK"
provider_code="HSTECH"
quote_code="HK.800700"
currency="HKD"
reference_history_source="none"
eastmoney_secid="124.HSTECH"
"""
    key = change.split("=")[0]
    text = "\n".join(line for line in text.splitlines() if not line.startswith(key + "="))
    path = tmp_path / "indices.toml"
    path.write_text(text + "\n" + change)
    with pytest.raises(DataError):
        load_indices(path)


def test_eastmoney_hk_mapping_validates_identity_and_close_time():
    data = {"market": 124, "code": "HSTECH", "klines": ["2026-10-06,4100,4200"]}
    row = parse_prices(data, HK, HK.quote_code, DAY, DAY, OBSERVED, "hk.json")[0]
    assert row.symbol == "HK.800700" and row.currency == "HKD"
    assert row.quote_at.hour == 16 and row.quote_at.minute == 10
    assert security_id(HK, HK.quote_code) == "124.HSTECH"
    with pytest.raises(DataError, match="wrong security"):
        parse_prices({**data, "code": "HSI"}, HK, HK.quote_code, DAY, DAY, OBSERVED, "bad")
    with pytest.raises(DataError, match="unfinished"):
        parse_prices(
            data, HK, HK.quote_code, DAY, DAY, datetime(2026, 10, 6, 8, 5, tzinfo=UTC), "bad"
        )
    with pytest.raises(DataError, match="explicit"):
        security_id(replace(HK, eastmoney_secid=None), HK.quote_code)


def test_hk_calendar_preserves_hk_holidays_and_archives_older_benchmark_sessions(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(
        sys.modules, "futu", SimpleNamespace(TradeDateMarket=SimpleNamespace(HK="HK"))
    )
    source = FutuSource(Archive(tmp_path), "localhost", 11111)
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        return 0, [
            {"time": DAY.isoformat(), "trade_date_type": "WHOLE"},
            {"time": "2017-08-23", "trade_date_type": "WHOLE"},
        ]

    source.ctx = SimpleNamespace(request_trading_days=request)
    prior = date(2015, 1, 2)

    def old_prices(spec, start, end):
        assert spec.quote_code == "HK.800000" and spec.currency == "HKD"
        assert start <= prior <= end
        return [SimpleNamespace(trade_date=prior, artifact_path="hsi.json")]

    monkeypatch.setattr(source, "index_history", old_prices)
    days, path = source.trading_days(prior, DAY, market="HK")
    assert days == {prior, DAY}
    assert calls[0]["market"] == "HK"
    payload = json.loads(open(path).read())
    assert payload["historical_reference_symbol"] == "HK.800000"
    assert payload["historical_reference_artifacts"] == ["hsi.json"]
    assert payload["calendar_corrections"][0]["date"] == "2017-08-23"
    assert "hkex.com.hk" in payload["calendar_corrections"][0]["source_url"]


def test_hk_backfill_does_not_query_mainland_calendar_or_reference(tmp_path):
    row = Observation(
        HK.quote_code, DAY, "futu", Decimal(4200), None, None, OBSERVED, OBSERVED, currency="HKD"
    )

    class Primary:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def trading_days(self, start, end, *, market):
            assert market == "HK"
            return {DAY}, "hk-calendar.json"

        def index_history(self, *_):
            return [row]

        def valuation_history(self, *_):
            return []

    saved = []
    repo = SimpleNamespace(
        save_observations=lambda _, rows: saved.extend(rows),
        save_source_valuations=lambda *_: None,
        weight_dates=lambda _: [],
    )
    report = {"sources": {}, "quarantined_rows": []}
    HistoricalBackfill(repo, Primary, None, None, tmp_path)._collect(HK, DAY, DAY, "run", report)
    assert saved == [row]
    assert report["trading_calendar"]["market"] == "HK"
    assert "baostock_price" not in report["sources"]
    assert "csindex_price" not in report["sources"]


def test_cnindex_uses_named_close_column_and_rejects_wrong_index():
    spec = IndexSpec("chinext", "创业板指", "399006", "SZ.399006", 100)
    data = {
        "indexCode": "399006",
        "item": ["timestamp", "open", "close"],
        "data": [["2026-10-06", 3000, "2958.3412"]],
    }
    rows = parse_history(data, spec, DAY, DAY, OBSERVED, "cnindex.json")
    assert rows[0].price == Decimal("2958.3412")
    assert rows[0].source == "cnindex" and rows[0].pe_ttm is None
    with pytest.raises(DataError, match="different index"):
        parse_history({**data, "indexCode": "399001"}, spec, DAY, DAY, OBSERVED, "bad")


def test_hk_valuation_keeps_native_points_and_requires_independent_price():
    row = Observation(
        HK.quote_code, DAY, "futu", Decimal(4200), None, None, OBSERVED, OBSERVED, currency="HKD"
    )
    pe = SourceValuation(
        "futu", HK.quote_code, DAY, "pe", "futu_valuation_pe", Decimal(20), OBSERVED, "pe.json"
    )
    result = derive_points(HK, [row, replace(row, source="eastmoney")], [pe])[0]
    assert result["implied_eps"] == 210 and result["price_status"] == "pass"
    assert result["reference_pe"] is None
    assert derive_points(HK, [row], [pe])[0]["implied_eps"] is None
    dashboard = build_dashboard(HK, derive_points(HK, [row], []))
    assert dashboard["index"]["currency"] == "HKD"
    csv_row = next(csv.DictReader(StringIO(series_csv(dashboard).lstrip("\ufeff"))))
    assert csv_row["pe"] == csv_row["band_8x"] == ""
