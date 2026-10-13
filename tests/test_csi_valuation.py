from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from index_valuation.domain import DataError, ReferenceHistory, SourceError
from index_valuation.history import HistoricalBackfill
from index_valuation.index_series import build_dashboard, derive_points, method_for
from index_valuation.providers.csindex_history import parse_history_row
from index_valuation.providers.danjuan_history import parse_history
from index_valuation.providers.eastmoney import parse_prices
from index_valuation.registry import load_indices

SPEC = load_indices()["divlowvol"]
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 13, 10, tzinfo=UTC)


def inputs():
    price, pe = parse_history_row(
        {"indexCode": "H30269", "tradeDate": "20261012", "close": 1000, "peg": 8},
        SPEC,
        NOW,
        "csi.json",
    )
    reference = replace(pe, source="danjuan", basis="danjuan_published_pe_ttm")
    return [price, replace(price, source="eastmoney")], pe, reference


def test_csi_price_index_mapping_is_distinct_from_etf_and_total_return():
    assert SPEC.provider_code == "H30269" and SPEC.quote_code == "CSI.H30269"
    assert SPEC.market == "CN" and SPEC.currency == "CNY" and not SPEC.futu_supported
    assert SPEC.inception_date == date(2013, 12, 19)
    assert SPEC.history_start_date == date(2005, 12, 30)
    payload = {"market": 2, "code": "H30269", "klines": ["2026-10-12,1000,1000"]}
    assert parse_prices(payload, SPEC, SPEC.quote_code, DAY, DAY, NOW, "raw")[0].price == 1000
    with pytest.raises(DataError, match="wrong security"):
        parse_prices({**payload, "code": "H20269"}, SPEC, SPEC.quote_code, DAY, DAY, NOW, "raw")


@pytest.mark.parametrize(
    "override",
    [
        "futu_supported=true",
        'provider_code="H20269"',
        'reference_history_source="none"',
        'danjuan_symbol="H30269"',
        'eastmoney_secid=""',
    ],
)
def test_csi_registry_rejects_uncollected_pe_or_mismatched_provider_identity(tmp_path, override):
    config = """[indices.test]
name="Test"
provider_code="H30269"
quote_code="CSI.H30269"
futu_supported=false
valuation_source="csindex"
reference_history_source="csindex"
eastmoney_secid="2.H30269"
additional_history_sources=["danjuan"]
danjuan_symbol="CSIH30269"
"""
    key = override.split("=")[0]
    lines = [line for line in config.splitlines() if not line.startswith(key + "=")]
    path = tmp_path / "indices.toml"
    path.write_text("\n".join([*lines, override]))
    with pytest.raises(DataError):
        load_indices(path)


def test_csi_primary_pe_never_compares_to_itself_or_uses_another_basis():
    prices, pe, reference = inputs()
    point = derive_points(SPEC, prices, [pe])[0]
    assert point["pe"] == 8 and point["implied_eps"] == 125
    assert point["pe_comparison_status"] == "missing" and point["reference_pe"] is None
    for other in (reference, replace(pe, basis="forward_pe")):
        assert derive_points(SPEC, prices, [other])[0]["pe"] is None
    assert method_for(SPEC) != method_for(load_indices()["csi300"])


def test_csi_material_pe_difference_blocks_only_that_day_without_filling_reference():
    prices, pe, reference = inputs()
    reference = replace(reference, value=Decimal(10))
    bad = derive_points(SPEC, prices, [pe, reference])[0]
    assert bad["pe"] == 8 and bad["reference_pe_source"] == "danjuan"
    assert not bad["pe_usable"] and bad["implied_eps"] is None
    next_day = date(2026, 10, 13)
    good = derive_points(
        SPEC,
        [replace(p, trade_date=next_day) for p in prices],
        [replace(pe, trade_date=next_day)],
    )[0]
    assert good["reference_pe"] is None and good["implied_eps"] == 125
    dashboard = build_dashboard(SPEC, [bad, good])
    assert dashboard["selected"]["history_count"] == 0
    assert dashboard["selected"]["fixed"]["8"] == 1000


def test_csi_statistics_use_daily_primary_pe_not_weekly_reference():
    prices, pe, reference = inputs()
    all_prices, all_values = [], []
    days = [date(2026, 9, d) for d in (7, 8, 9)]
    for day, value in zip(days, (6, 8, 10), strict=True):
        all_prices.extend(replace(p, trade_date=day) for p in prices)
        all_values.append(replace(pe, trade_date=day, value=Decimal(value)))
    all_values.append(replace(reference, trade_date=days[0], value=Decimal(6)))
    spec = replace(SPEC, band_min_observations=2)
    selected = build_dashboard(spec, derive_points(spec, all_prices, all_values))["selected"]
    assert selected["history_count"] == 2 and selected["mean_pe"] == 7
    assert selected["history_end"] == days[1]


def test_danjuan_china_mapping_keeps_local_date_and_rejects_stale_legacy_code():
    stamp = 1791734400000  # 2026-10-12 Shanghai midnight.
    history = {"result_code": 0, "data": {"index_eva_pe_growths": [{"ts": stamp, "pe": 8}]}}
    detail = {"result_code": 0, "data": {"index_code": "CSIH30269", "ts": stamp, "pe": 8}}
    rows = parse_history(history, detail, SPEC, DAY, DAY, NOW, "raw")
    assert len(rows) == 1 and rows[0].trade_date == DAY
    assert rows[0].symbol == SPEC.quote_code
    detail["data"]["index_code"] = "H30269"
    with pytest.raises(DataError, match="identity"):
        parse_history(history, detail, SPEC, DAY, DAY, NOW, "raw")


@pytest.mark.parametrize("calendar_mode", ["live", "cached", "fallback", "unavailable"])
def test_csi_collection_does_not_require_futu_quotes_for_mainland_calendar(tmp_path, calendar_mode):
    prices, pe, reference = inputs()

    class Secondary:
        def __enter__(self):
            if calendar_mode != "live":
                raise SourceError("BaoStock unavailable")
            return self

        def __exit__(self, *_):
            pass

        def trading_days(self, *_):
            return {DAY}, "live.json"

        def cached_trading_days(self, *_):
            if calendar_mode != "cached":
                raise SourceError("No covering cache")
            return {DAY}, "cached.json"

        def index_history(self, *_):
            pytest.fail("CSI namespace must not be passed to BaoStock's SH/SZ quotes")

    class CalendarOnly(Secondary):
        def __enter__(self):
            return self

        def trading_days(self, *_):
            return {DAY}, "fallback.json"

        def valuation_history(self, *_):
            pytest.fail("Calendar fallback must not request unsupported Futu PE")

    saved = []
    repo = SimpleNamespace(
        save_observations=lambda _, rows: saved.extend(rows),
        save_source_valuations=lambda *_: None,
        weight_dates=lambda _: [],
    )
    csi = SimpleNamespace(
        source_name="csindex", index_history=lambda *_: ReferenceHistory(prices[:1], [pe])
    )
    dj = SimpleNamespace(
        source_name="danjuan", index_history=lambda *_: ReferenceHistory(valuations=[reference])
    )
    job = HistoricalBackfill(
        repo,
        None,
        Secondary,
        csi,
        tmp_path,
        calendar_fallback_factory=CalendarOnly if calendar_mode == "fallback" else None,
        additional_sources=(dj,),
    )
    report = {"sources": {}, "quarantined_rows": []}
    if calendar_mode == "unavailable":
        with pytest.raises(SourceError, match="No calendar source"):
            job._collect(SPEC, DAY, DAY, "run", report)
        return
    job._collect(SPEC, DAY, DAY, "run", report)
    assert report["trading_calendar"]["artifact_path"] == f"{calendar_mode}.json"
    assert report["sources"]["futu_pe"]["status"] == "unsupported"
    assert report["sources"]["baostock_price"]["status"] == "unsupported"
    assert report["pe_primary_source"] == "csindex" and report["pe_reference_source"] == "danjuan"
    assert saved == prices[:1]
