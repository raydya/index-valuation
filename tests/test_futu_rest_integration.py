"""Production REST source policy, source isolation and reusable collection flow."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from index_valuation.domain import DataError, Observation, ReferenceHistory, SourceValuation
from index_valuation.history import HistoricalBackfill
from index_valuation.index_series import build_dashboard, derive_points, method_for, series_csv
from index_valuation.panic_reference import load_panic_reference, panic_reference_for
from index_valuation.registry import load_indices

SPECS = load_indices()
SPEC = SPECS["sp500"]
DAY = date(2024, 7, 5)
NOW = datetime(2024, 7, 10, tzinfo=UTC)


def price(source="futorest", value="5500", day=DAY):
    return Observation(
        SPEC.quote_code, day, source, Decimal(value), None, None, NOW, NOW, currency="USD"
    )


def valuation(metric="pe", value="20", day=DAY):
    return SourceValuation(
        "futorest",
        SPEC.quote_code,
        day,
        metric,
        f"futorest_valuation_{metric}",
        Decimal(value),
        NOW,
        "rest.json",
    )


def test_us_production_rest_sources_are_explicit_and_asia_policy_is_unchanged():
    for key, symbol, reference in (
        ("sp500", "US..SPX", "fred"),
        ("nasdaq100", "US..NDX", "nasdaq"),
    ):
        spec = SPECS[key]
        assert spec.valuation_source == spec.reference_history_source == "futorest"
        assert spec.futorest_symbol == spec.quote_code == symbol
        assert spec.price_sources == ("futorest", reference)
        assert spec.additional_history_sources == (reference,)
        assert not spec.futu_supported and spec.official_price_source is None
        assert spec.yahoo_proxy_symbol is None and spec.yahoo_symbol is None
        assert spec.band_min_observations == 252 and Decimal(8) in spec.band_multiples
        assert method_for(spec) == "futorest_index_implied_daily_v1"
    assert SPECS["csi300"].valuation_source == "futu" and SPECS["csi300"].futu_supported
    assert SPECS["hstech"].valuation_source == "futu" and SPECS["hstech"].futu_supported
    assert SPECS["divlowvol"].valuation_source == "csindex"
    assert not SPECS["ustotal"].collection_enabled
    assert SPECS["ustotal"].futorest_symbol is None
    assert "CRSPTM1" in SPECS["ustotal"].collection_disabled_reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("futorest_symbol", "US.SPX"),
        ("futorest_symbol", "US..NDX"),
        ("futorest_symbol", "US.SPY"),
        ("futu_supported", True),
        ("valuation_source", "yahooetf"),
        ("official_price_source", "fred"),
        ("additional_history_sources", ["yahoo"]),
        ("price_sources", ["fred", "futorest"]),
        ("price_sources", ["futorest"]),
        ("fred_series", "NASDAQ100"),
    ],
)
def test_rest_registry_rejects_wrong_identity_or_an_implicit_price_fallback(tmp_path, field, value):
    original = (
        Path("indices.toml").read_text().split("[indices.sp500]", 1)[1].split("[indices.", 1)[0]
    )
    lines = [line for line in original.splitlines() if not line.startswith(field + " =")]
    config = tmp_path / "indices.toml"
    config.write_text("[indices.test]\n" + "\n".join(lines) + f"\n{field} = {json.dumps(value)}\n")
    with pytest.raises(DataError):
        load_indices(config)


@pytest.mark.parametrize(
    "reference,status,eps",
    [
        (None, "unverified", None),
        ("5500", "pass", Decimal(275)),
        ("5610", "warning", Decimal(275)),
        ("6000", "error", None),
    ],
)
def test_rest_price_requires_a_real_same_day_reference(reference, status, eps):
    prices = [price()] + ([price("fred", reference)] if reference else [])
    point = derive_points(SPEC, prices, [valuation()])[0]
    assert point["price"] == 5500 and point["price_source"] == "futorest"
    assert point["price_status"] == status and point["implied_eps"] == eps
    assert point["pe"] == 20 and point["pe_source"] == "futorest"
    assert point["pe_usable"] and point["pe_comparison_status"] == "missing"
    assert point["basis_status"] == "unconfirmed" and point["pe_proxy_symbol"] is None


def test_missing_rest_price_is_not_filled_with_official_or_retired_prices():
    prices = [price("fred"), price("yahoo"), price("eastmoney"), price("futu")]
    point = derive_points(SPEC, prices, [valuation()])[0]
    assert point["price"] is None and point["price_source"] is None
    assert point["price_status"] == "missing" and not point["price_usable"]
    assert point["implied_eps"] is None and point["pe"] == 20
    assert point["source_prices"] == {"fred": Decimal(5500)}
    assert derive_points(SPEC, prices, []) == []


def test_legacy_prices_valuations_and_dates_cannot_contaminate_new_rest_method():
    old_day = DAY - timedelta(days=1)
    old_pe = replace(
        valuation(day=old_day),
        source="yahooetf",
        metric="pe_ttm_proxy",
        basis="yahoo_etf_spy_trailing_pe_proxy",
        value=Decimal(999),
    )
    old_pb = replace(valuation("pb", "999"), source="futu", basis="futu_valuation_pb")
    prices = [price(), price("fred"), price("yahoo", "9999"), price("yahoo", day=old_day)]
    points = derive_points(
        SPEC, prices, [valuation(), valuation("pb", "4"), valuation("ps", "3"), old_pe, old_pb]
    )
    assert len(points) == 1 and points[0]["trade_date"] == DAY
    point = points[0]
    assert point["price_status"] == "pass" and point["pe"] == 20
    assert point["pb"] == 4 and point["ps"] == 3 and point["pe_references"] == []
    data = build_dashboard(SPEC, points)
    assert data["selected"]["fixed"]["8"] == 2200
    assert "TTM EPS" in data["basis_note"] and "不称为" in data["basis_note"]
    assert "futorest_valuation_pe" in series_csv(data)
    panic = panic_reference_for(data, load_panic_reference(), SPECS)
    assert panic["comparison"]["status"] == "different_basis"
    assert panic["comparison"]["reference_points"] is None


@pytest.mark.parametrize("pe,eps", [("0", None), ("NaN", None), ("-20", Decimal(-275))])
def test_zero_invalid_and_negative_pe_are_not_positive_bands(pe, eps):
    point = derive_points(SPEC, [price(), price("fred")], [valuation(value=pe)])[0]
    assert point["implied_eps"] == eps
    assert build_dashboard(SPEC, [point])["selected"]["fixed"] == {}


def test_rest_statistics_use_252_prior_daily_pe_even_when_price_is_missing():
    days = []
    day = date(2023, 1, 2)
    while len(days) < 254:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    inputs = [valuation(day=day, value="20" if i < 252 else "100") for i, day in enumerate(days)]
    points = derive_points(SPEC, [], inputs)
    assert build_dashboard(SPEC, points, as_of=days[251])["selected"]["mean_pe"] is None
    selected = build_dashboard(SPEC, points, as_of=days[252])["selected"]
    assert selected["mean_pe"] == 20 and selected["history_count"] == 252
    assert selected["percentile"] == 100 and selected["mean_points"] is None
    assert build_dashboard(SPEC, points[:-1])["selected"] == selected


def test_multi_metric_reference_collection_keeps_calendar_and_metric_identity(tmp_path):
    weekend = DAY + timedelta(days=1)
    batch = ReferenceHistory(
        [price(), price(day=weekend)],
        [valuation(metric, day=day) for metric in ("pe", "pb", "ps") for day in (DAY, weekend)],
    )
    source = SimpleNamespace(source_name="futorest", index_history=lambda *_: batch)
    calendar = SimpleNamespace(source_name="nyse", trading_days=lambda *a, **k: ({DAY}, "calendar"))
    saved_prices, saved_values = [], []
    repo = SimpleNamespace(
        save_observations=lambda _, rows: saved_prices.extend(rows),
        save_source_valuations=lambda _, rows: saved_values.extend(rows),
        weight_dates=lambda _: [],
    )
    report = {"sources": {}, "quarantined_rows": []}
    HistoricalBackfill(repo, None, None, source, tmp_path, calendar_source=calendar)._collect(
        SPEC, DAY, weekend, "run", report
    )
    assert saved_prices == [price()] and {r.metric for r in saved_values} == {"pe", "pb", "ps"}
    assert all(row.trade_date == DAY for row in saved_values)
    assert len(report["quarantined_rows"]) == 4
    assert report["sources"]["futu_price"]["status"] == "unsupported"
    for metric in ("pe", "pb", "ps"):
        assert report["sources"][f"futorest_{metric}"]["rows"] == 1
    assert report["pe_primary_source"] == "futorest"


def test_incremental_rest_history_uses_new_method_and_overlap_from_last_stored_day():
    from index_valuation.cli import incremental_start

    assert incremental_start(SPEC, []) == SPEC.history_start_date
    assert incremental_start(SPEC, [{"trade_date": DAY, "pe": None}]) == SPEC.history_start_date
    assert incremental_start(SPEC, [{"trade_date": DAY, "pe": Decimal(20)}]) == DAY - timedelta(
        days=14
    )


def test_rest_metric_failure_does_not_misreport_successful_price_or_pe(tmp_path):
    batch = ReferenceHistory(
        [price()], [valuation()], [{"stage": "pb", "category": "permission_denied"}]
    )
    source = SimpleNamespace(source_name="futorest", index_history=lambda *_: batch)
    calendar = SimpleNamespace(source_name="nyse", trading_days=lambda *a, **k: ({DAY}, "calendar"))
    repo = SimpleNamespace(
        save_observations=lambda *_: None,
        save_source_valuations=lambda *_: None,
        weight_dates=lambda _: [],
    )
    report = {"sources": {}, "quarantined_rows": []}
    HistoricalBackfill(repo, None, None, source, tmp_path, calendar_source=calendar)._collect(
        SPEC, DAY, DAY, "run", report
    )
    assert report["sources"]["futorest_price"]["status"] == "ok"
    assert report["sources"]["futorest_pe"]["status"] == "ok"
    assert report["sources"]["futorest_pb"]["status"] == "partial"
    assert report["sources"]["futorest_pb"]["rows"] == 0


def test_paused_collection_is_explicit_in_index_list_and_archived_dashboard():
    from fastapi.testclient import TestClient

    from index_valuation.webapp import create_app

    spec = SPECS["ustotal"]
    point = {
        "trade_date": DAY,
        "price": Decimal(1000),
        "pe": Decimal(20),
        "implied_eps": Decimal(50),
        "price_source": "yahoo",
        "pe_source": "yahooetf",
        "pe_basis": "yahoo_etf_vti_trailing_pe_proxy",
        "pe_proxy_symbol": "VTI",
        "pe_comparison_status": "missing",
    }
    repo = SimpleNamespace(index_series=lambda *_: [point])
    with TestClient(create_app(repository=repo, specs={spec.key: spec})) as client:
        listed = client.get("/api/indices").json()[0]
        data = client.get("/api/indices/ustotal/dashboard").json()
    assert listed["collection_enabled"] is data["index"]["collection_enabled"] is False
    assert listed["collection_disabled_reason"] == spec.collection_disabled_reason
    assert data["method"] == "yahoo_vti_proxy_yahoo_google_daily_v1"
    assert data["selected"]["pe_source"] == "yahooetf"
    assert "历史归档" in data["basis_note"] and "不代表当前行情" in data["basis_note"]


@pytest.mark.parametrize("flag,reason", [("false", '""'), ('"false"', '"reason"')])
def test_paused_collection_requires_a_boolean_and_specific_reason(tmp_path, flag, reason):
    config = tmp_path / "indices.toml"
    config.write_text(
        '[indices.test]\nname="Test"\nprovider_code="000300"\nquote_code="SH.000300"\n'
        f"collection_enabled={flag}\ncollection_disabled_reason={reason}\n"
    )
    with pytest.raises(DataError, match="Collection|paused"):
        load_indices(config)
