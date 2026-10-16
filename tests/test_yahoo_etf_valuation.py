import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, Observation, SourceValuation
from index_valuation.index_series import build_dashboard, derive_points, method_for
from index_valuation.panic_reference import load_panic_reference, panic_reference_for
from index_valuation.providers.yahoo_etf_valuation import (
    DATE_BASIS,
    YahooETFValuation,
    basis_for,
    parse_snapshot,
)
from index_valuation.registry import load_indices

SPECS = load_indices()
# Legacy inputs and method remain readable after production moves to Futu REST.
for key, proxy_symbol, official, yahoo_symbol in (
    ("sp500", "SPY", "fred", "^GSPC"),
    ("nasdaq100", "QQQ", "nasdaq", "^NDX"),
    ("ustotal", "VTI", None, "^CRSPTM1"),
):
    SPECS[key] = replace(
        SPECS[key],
        valuation_source="yahooetf",
        futorest_symbol=None,
        yahoo_proxy_symbol=proxy_symbol,
        yahoo_symbol=yahoo_symbol,
        reference_history_source=official or "yahoo",
        official_price_source=official,
        price_sources=(official, "yahoo") if official else ("yahoo", "google"),
        additional_history_sources=(("yahoo" if official else "google"), "yahooetf"),
    )
SPEC = SPECS["nasdaq100"]
DAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 15, 1, tzinfo=UTC)


def page(*, symbol="QQQ", quote_type="ETF", value="27.912027", **quote_fields):
    quote = {
        "symbol": symbol,
        "quoteType": quote_type,
        "currency": "USD",
        "regularMarketTime": int(datetime(2026, 10, 14, 20, tzinfo=UTC).timestamp()),
        "marketState": "POST",
        "preMarketTime": int(NOW.timestamp()),
        **quote_fields,
    }
    summary = {
        "price": quote,
        "summaryDetail": {"trailingPE": {"raw": value}, "forwardPE": {"raw": 999}},
        "topHoldings": {"equityHoldings": {"priceToEarnings": {"raw": 0.04}}},
    }
    body = json.dumps({"quoteSummary": {"result": [summary], "error": None}})
    return '<script type="application/json">' + json.dumps({"body": body}) + "</script>"


def prices(spec=SPEC, sources=None, day=DAY):
    return [
        Observation(
            spec.quote_code, day, source, Decimal(value), None, None, NOW, NOW, currency="USD"
        )
        for source, value in (sources or [(spec.official_price_source, "1000"), ("yahoo", "1000")])
    ]


def proxy(spec=SPEC, value="20", day=DAY):
    return SourceValuation(
        "yahooetf",
        spec.quote_code,
        day,
        "pe_ttm_proxy",
        basis_for(spec.yahoo_proxy_symbol),
        Decimal(value),
        NOW,
        "proxy.json",
        raw={"proxy_symbol": spec.yahoo_proxy_symbol, "date_basis": DATE_BASIS},
    )


@pytest.mark.parametrize(
    "key,ticker,official",
    [("sp500", "SPY", "fred"), ("nasdaq100", "QQQ", "nasdaq"), ("ustotal", "VTI", None)],
)
def test_legacy_proxy_configuration_and_decimal_snapshot_are_explicit(key, ticker, official):
    spec = SPECS[key]
    assert spec.valuation_source == "yahooetf"
    assert spec.yahoo_proxy_symbol == ticker and spec.official_price_source == official
    assert spec.price_sources == ((official, "yahoo") if official else ("yahoo", "google"))
    assert spec.eastmoney_secid is None and spec.historical_price_source == "none"
    assert spec.additional_history_sources == (("yahoo" if official else "google"), "yahooetf")
    assert spec.band_min_observations == 252
    # A bare JSON number must retain all its decimal digits.
    text = page(symbol=ticker).replace('\\"27.912027\\"', "27.912027")
    row = parse_snapshot(text, spec, DAY, DAY, NOW, "page.json")[0]
    assert row.value == Decimal("27.912027")
    assert row.symbol == spec.quote_code and row.metric == "pe_ttm_proxy"
    assert row.raw["proxy_symbol"] == ticker
    assert row.raw["pe_effective_date"] is None and row.raw["date_basis"] == DATE_BASIS
    assert row.trade_date == DAY and row.observed_at == NOW


@pytest.mark.parametrize(
    "reference,status,usable",
    [
        ([], "unverified", False),
        ([("google", "1000")], "pass", True),
        ([("google", "1020")], "warning", True),
        ([("google", "2000")], "error", False),
    ],
)
def test_nonofficial_proxy_prices_need_a_same_day_cross_check(reference, status, usable):
    spec = SPECS["ustotal"]
    point = derive_points(
        spec, prices(spec, sources=[("yahoo", "1000"), *reference]), [proxy(spec)]
    )[0]
    assert point["price_source"] == "yahoo"
    assert point["price_status"] == status and point["price_usable"] is usable
    assert point["pe"] == 20 and point["pe_proxy_symbol"] == "VTI"
    assert point["implied_eps"] == (Decimal(50) if usable else None)
    data = build_dashboard(spec, [point])
    assert data["selected"]["fixed"] == (
        {str(m): 50 * m for m in spec.band_multiples} if usable else {}
    )
    assert data["method"] == "yahoo_vti_proxy_yahoo_google_daily_v1"
    assert "Google Finance" in data["basis_note"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("google_symbol", "CRSPTMT:INDEXNASDAQ"),
        ("google_symbol", "VTI:NYSEARCA"),
        ("yahoo_proxy_symbol", "SPY"),
        ("additional_history_sources", ["yahooetf"]),
        ("official_price_source", "google"),
    ],
)
def test_vti_proxy_registry_rejects_wrong_or_missing_price_reference(tmp_path, field, value):
    from pathlib import Path

    original = Path("indices.toml").read_text().split("[indices.ustotal]", 1)[1]
    lines = [line for line in original.splitlines() if not line.startswith(field + " =")]
    config = tmp_path / "indices.toml"
    config.write_text("[indices.test]\n" + "\n".join(lines) + f"\n{field} = {json.dumps(value)}\n")
    with pytest.raises(DataError):
        load_indices(config)


@pytest.mark.parametrize(
    "kwargs", [{"symbol": "SPY"}, {"quote_type": "INDEX"}, {"currency": "HKD"}]
)
def test_proxy_rejects_wrong_security_type_or_currency(kwargs):
    with pytest.raises(DataError, match="identity"):
        parse_snapshot(page(**kwargs), SPEC, DAY, DAY, NOW, "bad")


def test_proxy_does_not_fill_old_dates_or_use_a_live_or_future_session():
    assert (
        parse_snapshot(page(), SPEC, DAY - timedelta(days=3), DAY - timedelta(days=1), NOW, "old")
        == []
    )
    assert parse_snapshot(page(marketState="REGULAR"), SPEC, DAY, DAY, NOW, "live") == []
    assert parse_snapshot(page(), SPEC, DAY, DAY, NOW.replace(day=14, hour=19), "future") == []
    # PRE and POST timestamps never change the associated regular session.
    row = parse_snapshot(page(marketState="PRE"), SPEC, DAY, DAY, NOW.replace(hour=11), "pre")[0]
    assert row.trade_date == DAY


@pytest.mark.parametrize("value", [None, "0", "-1", "NaN", "Infinity"])
def test_proxy_missing_trailing_pe_never_uses_forward_pe_or_holdings_yield(value):
    assert parse_snapshot(page(value=value), SPEC, DAY, DAY, NOW, "missing") == []


def test_proxy_rejects_ambiguous_pages_or_bad_timestamps():
    for text in (
        page() + page(),
        "<html>consent page</html>",
        page(regularMarketTime=None),
        page(regularMarketTime=10**30),
    ):
        with pytest.raises(DataError):
            parse_snapshot(text, SPEC, DAY, DAY, NOW, "bad")


def test_http_failure_is_archived_and_does_not_generate_a_valuation(tmp_path, monkeypatch):
    def get(url, **kwargs):
        return httpx.Response(429, text="rate limited", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(httpx.HTTPStatusError):
        YahooETFValuation(Archive(tmp_path)).index_history(SPEC, DAY, DAY)
    archive = list(tmp_path.rglob("*.json"))
    assert len(archive) == 1 and json.loads(archive[0].read_text())["status"] == 429


@pytest.mark.parametrize("reference,status", [([], "unverified"), ([("yahoo", "2000")], "error")])
def test_official_price_remains_usable_when_yahoo_is_missing_or_disagrees(reference, status):
    point = derive_points(SPEC, prices(sources=[("nasdaq", "1000"), *reference]), [proxy()])[0]
    assert point["price"] == 1000 and point["price_source"] == "nasdaq"
    assert point["price_status"] == status and point["price_usable"]
    assert point["pe"] == 20 and point["implied_eps"] == 50
    assert point["basis_status"] == "etf_proxy"
    assert build_dashboard(SPEC, [point])["selected"]["fixed"]["8"] == 400


def test_deprecated_inputs_do_not_affect_proxy_values_dates_or_quality():
    old_day = DAY - timedelta(days=1)
    old_pe = SourceValuation(
        "danjuan",
        SPEC.quote_code,
        old_day,
        "pe_ttm",
        "danjuan_published_pe_ttm",
        Decimal(100),
        NOW,
        "old",
    )
    other = replace(old_pe, source="gurufocus", trade_date=DAY, value=Decimal(999))
    rows = derive_points(
        SPEC,
        prices(
            sources=[("nasdaq", "1000"), ("yahoo", "1000"), ("eastmoney", "9999"), ("fred", "9999")]
        ),
        [proxy(), old_pe, other],
    )
    assert len(rows) == 1 and rows[0]["price_status"] == "pass"
    assert set(rows[0]["source_prices"]) == {"nasdaq", "yahoo"}
    assert rows[0]["pe_references"] == [] and rows[0]["implied_eps"] == 50
    assert build_dashboard(SPEC, rows)["selected"]["history_count"] == 0


def test_no_official_price_means_no_yahoo_substitution_or_derived_band():
    point = derive_points(SPEC, prices(sources=[("yahoo", "1000")]), [proxy()])[0]
    assert point["price"] is None and point["pe"] == 20
    assert point["price_status"] == "missing" and not point["price_usable"]
    assert point["implied_eps"] is None
    assert derive_points(SPEC, prices(sources=[("yahoo", "1000")]), []) == []


def test_proxy_history_and_panic_reference_do_not_mix_with_retired_basis():
    point = derive_points(SPEC, prices(), [proxy()])[0]
    data = build_dashboard(SPEC, [point])
    assert data["selected"]["mean_pe"] is None and data["selected"]["percentile"] is None
    result = panic_reference_for(data, load_panic_reference(), SPECS)
    assert result["comparison"]["status"] == "different_basis"
    assert result["comparison"]["reference_points"] is None
    assert result["record"]["source"] == "danjuan"
    assert method_for(SPEC) != "danjuan_index_implied_weekly_v1"
    assert method_for(SPEC) != method_for(replace(SPEC, yahoo_proxy_symbol="SPY"))


@pytest.mark.parametrize(
    "change",
    [
        'yahoo_proxy_symbol = "QQQ"',
        'official_price_source = "yahoo"',
        'price_sources = ["fred", "eastmoney"]',
        'fred_series = "NASDAQ100"',
        'additional_history_sources = ["yahoo"]',
    ],
)
def test_registry_rejects_uncollected_or_mismatched_proxy_sources(tmp_path, change):
    text = """[indices.test]
name = "Test"
provider_code = "SPX"
quote_code = "US..SPX"
currency = "USD"
timezone = "America/New_York"
futu_supported = false
valuation_source = "yahooetf"
yahoo_proxy_symbol = "SPY"
reference_history_source = "fred"
fred_series = "SP500"
additional_history_sources = ["yahoo", "yahooetf"]
yahoo_symbol = "^GSPC"
official_price_source = "fred"
price_sources = ["fred", "yahoo"]
"""
    key = change.split("=")[0].strip()
    text = "\n".join(line for line in text.splitlines() if not line.startswith(key + " ="))
    config = tmp_path / "indices.toml"
    config.write_text(text + "\n" + change)
    with pytest.raises(DataError):
        load_indices(config)
