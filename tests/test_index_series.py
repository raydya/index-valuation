import csv
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from fastapi.testclient import TestClient

from index_valuation.domain import DataError, IndexSpec, Observation, SourceValuation
from index_valuation.index_series import build_dashboard, derive_points, series_csv
from index_valuation.providers.futu import parse_valuation_history
from index_valuation.webapp import create_app

DAY = date(2020, 1, 2)
NOW = datetime(2020, 1, 10, 12, tzinfo=UTC)
SPEC = IndexSpec("test", "Test Index", "000300", "SH.000300", 300, band_min_observations=2)


def price(source="futu", value="1000", day=DAY):
    return Observation(SPEC.quote_code, day, source, Decimal(value), None, None, NOW, NOW)


def pe(value="20", day=DAY, source="futu"):
    return SourceValuation(
        source,
        SPEC.quote_code,
        day,
        "pe" if source == "futu" else "pe_ttm",
        "futu_valuation_pe" if source == "futu" else "csindex_published_rolling_pe",
        Decimal(value),
        NOW,
        f"{source}-pe.json",
    )


def points(values=("10", "20", "100", "10")):
    prices, valuations = [], []
    for i, value in enumerate(values):
        day = DAY + timedelta(days=i)
        prices.extend([price(day=day), price("csindex", day=day)])
        valuations.append(pe(value, day))
    return derive_points(SPEC, prices, valuations)


def test_direct_index_needs_no_constituents_and_keeps_futu_pe_basis():
    spec = replace(SPEC, inception_date=date(2020, 1, 3))
    result = derive_points(
        spec, [price("csindex"), price("baostock")], [pe(), pe("30", source="csindex")]
    )[0]
    assert result["implied_eps"] == 50
    assert result["price_source"] == "csindex"
    assert result["pe_source"] == "futu"
    assert result["pe"] == 20
    assert result["reference_pe"] == 30
    assert result["pe_comparison_status"] == "error"
    assert result["basis_status"] == "unconfirmed"
    assert result["phase"] == "backcast"
    assert "eps_ttm" not in result


def test_missing_futu_pe_is_not_filled_with_reference_or_previous_day():
    data = derive_points(
        SPEC,
        [price(), price("csindex")],
        [pe("30", source="csindex"), pe(day=DAY - timedelta(days=1))],
    )
    assert data[-1]["pe"] is None
    assert data[-1]["implied_eps"] is None
    assert data[0]["price"] is None


@pytest.mark.parametrize("reference", [[], [price("csindex", "2000")]])
def test_missing_or_conflicting_price_reference_blocks_derived_eps(reference):
    result = derive_points(SPEC, [price(), *reference], [pe()])[0]
    assert result["pe"] == 20
    assert result["implied_eps"] is None
    assert result["price_status"] in {"error", "unverified"}


@pytest.mark.parametrize(
    "invalid",
    [
        replace(price(), symbol="SH.600000"),
        replace(price(), currency="USD"),
        replace(price(), price_basis="forward_adjusted"),
        replace(price(), price=Decimal("NaN")),
    ],
)
def test_incompatible_prices_are_rejected(invalid):
    with pytest.raises(DataError):
        derive_points(SPEC, [invalid], [pe()])


def test_quantiles_exclude_same_day_and_future_and_ties_use_midrank():
    data = points()
    dashboard = build_dashboard(SPEC, data, as_of=DAY + timedelta(days=2), chart_years=0)
    selected = dashboard["selected"]
    assert selected["quantile_pe"]["0.5"] == 15
    assert selected["percentile"] == 100
    assert selected["history_count"] == 2
    assert selected["fixed"]["10"] == 100
    assert selected["quantile_points"]["0.5"] == 150
    assert dashboard["series"][0]["quantile_points"] == {}
    with_future = data + points(("9000",))
    # Changing the future date cannot change the earlier calculation.
    with_future[-1] = {**with_future[-1], "trade_date": date(2021, 1, 1)}
    assert build_dashboard(SPEC, with_future, as_of=DAY + timedelta(days=2))["selected"] == selected
    assert build_dashboard(SPEC, data)["selected"]["percentile"] == Decimal(1) / 6 * 100


def test_mean_uses_prior_positive_daily_pe_with_equal_weight_and_no_future_data():
    data = points(("10", "10", "70", "0", "-2", "20", "9000"))
    # A valid historical PE still contributes when that day's price is unavailable.
    data[0] = {**data[0], "price": None, "implied_eps": None}
    day = DAY + timedelta(days=5)
    dashboard = build_dashboard(SPEC, data, as_of=day, chart_years=0)
    selected = dashboard["selected"]
    assert selected["mean_pe"] == 30
    assert selected["mean_points"] == 1500
    assert selected["history_count"] == 3
    assert dashboard["series"][0]["mean_pe"] is None
    assert dashboard["series"][1]["mean_pe"] is None
    assert dashboard["series"][2]["mean_pe"] == 10
    assert dashboard["series"][3]["mean_pe"] == 30
    assert dashboard["series"][3]["mean_points"] is None
    assert dashboard["series"][4]["mean_pe"] == 30
    assert dashboard["series"][4]["mean_points"] is None
    assert build_dashboard(SPEC, data[:-1], as_of=day)["selected"] == selected


def test_mean_rolls_with_inclusive_window_start_and_ignores_chart_range():
    data = points(("1000", "10", "30", "100"))
    dates = (date(2010, 1, 1), date(2015, 1, 4), date(2018, 1, 2), date(2020, 1, 4))
    data = [{**point, "trade_date": day} for point, day in zip(data, dates, strict=True)]
    selected = build_dashboard(SPEC, data, history_years=5, chart_years=1)["selected"]
    assert selected["mean_pe"] == 20
    assert selected["mean_points"] == 200
    assert selected["history_span_short"] is False
    assert selected["history_start"] == date(2015, 1, 4)
    assert selected["history_end"] == date(2018, 1, 2)
    assert build_dashboard(SPEC, data, chart_years=0)["selected"] == selected
    assert build_dashboard(SPEC, data, history_years=0)["selected"]["mean_pe"] == (
        Decimal(1040) / 3
    )
    short = build_dashboard(SPEC, data, history_years=3)["selected"]
    assert short["history_count"] == 1
    assert short["mean_pe"] is None
    assert short["mean_points"] is None


def test_short_valuation_history_is_visible_without_filling_prelaunch_pe():
    data = points()
    data[0] = {**data[0], "pe": None, "implied_eps": None, "phase": "backcast"}
    selected = build_dashboard(SPEC, data, history_years=5)["selected"]
    assert selected["mean_pe"] == 60
    assert selected["history_count"] == 2
    assert selected["history_start"] == DAY + timedelta(days=1)
    assert selected["history_span_short"] is True
    assert build_dashboard(SPEC, data, history_years=0)["selected"]["history_span_short"] is False


def test_negative_pe_preserves_negative_eps_but_has_no_positive_valuation_band():
    dashboard = build_dashboard(SPEC, points(("10", "20", "-2")))
    assert dashboard["selected"]["implied_eps"] == -500
    assert dashboard["selected"]["fixed"] == {}
    assert dashboard["selected"]["quantile_points"] == {}
    assert dashboard["selected"]["percentile"] is None


def test_window_boundary_and_weekend_date_resolution():
    data = points(("10", "20", "30"))
    data[0]["trade_date"] = date(2010, 1, 2)
    dashboard = build_dashboard(SPEC, data, as_of=date(2020, 1, 5), history_years=5)
    assert dashboard["selected"]["trade_date"] == date(2020, 1, 4)
    assert dashboard["selected"]["history_count"] == 1
    assert dashboard["selected"]["percentile"] is None
    assert build_dashboard(SPEC, data, history_years=0)["selected"]["history_count"] == 2


def test_pb_and_ps_are_preserved_separately_and_do_not_become_pe():
    for metric in ("PB", "PS"):
        data = {
            "valuation_type": metric,
            "trend": {"historical_items": [{"time_str": str(DAY), "value": 2}]},
        }
        rows = parse_valuation_history(data, SPEC, DAY, DAY, NOW, "extra.json", metric)
        point = derive_points(SPEC, [], rows)[0]
        assert point[metric.lower()] == 2
        assert point["pe"] is None and point["implied_eps"] is None


def test_web_api_and_csv_use_same_reproducible_data_without_credentials(tmp_path):
    class MemoryRepository:
        def index_series(self, key, method):
            return points()

        def panic_lines(self, specs, as_of=None):
            return {}

    app = create_app(repository=MemoryRepository(), specs={SPEC.key: SPEC})
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/indices").json()[0]["key"] == "test"
        response = client.get("/api/indices/test/dashboard?as_of=2020-01-04&chart_years=0")
        assert response.status_code == 200
        payload = response.json()
        assert payload["selected"]["implied_eps"] == "10"
        assert payload["selected"]["mean_pe"] == "15"
        assert payload["selected"]["mean_points"] == "150"
        assert Decimal(payload["price_return"]["annualized_return_pct"]) == 0
        assert payload["price_return"]["start_date"] == "2020-01-02"
        assert payload["price_return"]["end_date"] == "2020-01-04"
        assert payload["price_return"]["basis"] == "price_return_excluding_dividends"
        assert len(payload["series"]) == 3
        output = client.get("/api/indices/test/export.csv?as_of=2020-01-04&chart_years=0")
        assert output.status_code == 200
        rows = list(csv.DictReader(StringIO(output.text.lstrip("\ufeff"))))
        assert rows[-1]["band_10x"] == "100"
        assert rows[-1]["quantile_0.5"] == "150.0"
        assert rows[-1]["mean_pe"] == payload["selected"]["mean_pe"]
        assert rows[-1]["mean_points"] == payload["selected"]["mean_points"]
        assert rows[-1]["history_years"] == "5"
        assert rows[-1]["history_start"] == "2020-01-02"
        assert rows[-1]["history_end"] == "2020-01-03"
        assert rows[-1]["price_source"] == "futu"
        assert rows[-1]["pe_source"] == "futu"
        assert rows[-1]["basis_status"] == "unconfirmed"
        assert rows[0]["price"] == payload["price_return"]["start_price"]
        assert rows[-1]["price"] == payload["price_return"]["end_price"]
        assert client.get("/api/indices/missing/dashboard").status_code == 404
        assert client.get("/api/indices/test/dashboard?chart_years=2").status_code == 422
        assert client.get("/api/indices/test/dashboard?as_of=1900-01-01").status_code == 422
        assert client.get("/api/indices/test/dashboard?as_of=bad").status_code == 422
        assert client.get("/.env").status_code == 404
        assert client.post("/api/indices/test/dashboard").status_code == 405


def test_csv_retains_missing_values_and_provenance():
    data = points(("0",))
    output = series_csv(build_dashboard(SPEC, data))
    row = list(csv.DictReader(StringIO(output.lstrip("\ufeff"))))[0]
    assert row["implied_eps"] == ""
    assert row["pe"] == ""
    assert row["mean_pe"] == ""
    assert row["mean_points"] == ""
    assert row["price_source"] == "futu"
