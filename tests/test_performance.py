from datetime import date
from decimal import Decimal

import pytest

from index_valuation.index_series import build_dashboard
from index_valuation.performance import price_return_summary
from index_valuation.registry import load_indices


def point(day, price, **kwargs):
    return {
        "trade_date": date.fromisoformat(day),
        "price": Decimal(price) if price is not None else None,
        "price_status": "pass",
        "price_source": "csindex",
        "phase": "published",
        "pe": None,
        "implied_eps": None,
        "pe_comparison_status": "missing",
        **kwargs,
    }


@pytest.mark.parametrize("last,expected", [("146.41", "10"), ("65.61", "-10"), ("100", "0")])
def test_compound_return_uses_actual_calendar_days_including_leap_day(last, expected):
    rows = [point("2020-01-01", "100"), point("2024-01-01", last)]
    result = price_return_summary(rows, date(2020, 1, 1), date(2024, 1, 1))
    assert result["elapsed_days"] == 1461  # Exactly four 365.25-day years.
    assert abs(result["annualized_return_pct"] - Decimal(expected)) < Decimal("1e-25")
    assert result["basis"] == "price_return_excluding_dividends"
    assert result["status"] == "ok"


def test_endpoints_are_actual_in_range_prices_without_filling_or_future_data():
    rows = [
        point("2019-12-31", "1"),
        point("2020-01-01", None),
        point("2020-01-02", "100"),
        point("2024-01-02", "146.41"),
        point("2024-01-03", None),
        point("2024-01-04", "10000"),
    ]
    result = price_return_summary(rows, date(2020, 1, 1), date(2024, 1, 3))
    assert result["start_date"] == date(2020, 1, 2)
    assert result["end_date"] == date(2024, 1, 2)
    assert result["start_price"] == 100 and result["end_price"] == Decimal("146.41")
    assert abs(result["annualized_return_pct"] - 10) < Decimal("1e-25")
    assert result == price_return_summary(rows[:-1], date(2020, 1, 1), date(2024, 1, 3))


@pytest.mark.parametrize("rows", [[], [point("2024-01-01", None)], [point("2024-01-01", "100")]])
def test_missing_or_single_date_never_becomes_zero_return(rows):
    result = price_return_summary(rows, date(2020, 1, 1), date(2024, 1, 1))
    assert result["status"] == "insufficient_data"
    assert result["annualized_return_pct"] is None


@pytest.mark.parametrize(
    "field,value", [("price", Decimal(0)), ("price", Decimal("NaN")), ("price_status", "error")]
)
def test_bad_endpoint_blocks_return_instead_of_silently_shortening_window(field, value):
    rows = [
        {**point("2020-01-01", "100"), field: value},
        point("2021-01-01", "110"),
        point("2024-01-01", "146.41"),
    ]
    result = price_return_summary(rows, date(2020, 1, 1), date(2024, 1, 1))
    assert result["start_date"] == date(2020, 1, 1)
    assert result["annualized_return_pct"] is None
    assert result["price_status"] == "error"


def test_single_source_prices_and_backcast_are_visible_independent_of_pe_quality():
    rows = [
        point("2020-01-01", "100", price_status="unverified", phase="backcast"),
        point("2024-01-01", "146.41", pe_usable=False, pe_comparison_status="error"),
    ]
    result = price_return_summary(rows, date(2020, 1, 1), date(2024, 1, 1))
    assert result["status"] == "ok" and result["price_status"] == "unverified"
    assert result["includes_backcast"] is True
    assert result["annualized_return_pct"] is not None


def test_return_follows_chart_window_and_historical_date_not_pe_statistics_window():
    rows = [
        point("2010-01-04", "50"),
        point("2015-01-05", "100"),
        point("2019-01-04", "180"),
        point("2020-01-04", "200"),
        point("2025-01-06", "9000"),
    ]
    spec = load_indices()["csi300"]
    day = date(2020, 1, 4)
    five = build_dashboard(spec, rows, as_of=day)["price_return"]
    assert five["start_date"] == date(2015, 1, 5)
    assert five["end_date"] == day and five["end_price"] == 200
    assert five == build_dashboard(spec, rows, as_of=day, history_years=10)["price_return"]
    one = build_dashboard(spec, rows, as_of=day, chart_years=1)["price_return"]
    assert one["start_date"] == date(2019, 1, 4)
    assert one["annualized_return_pct"] != five["annualized_return_pct"]
    all_history = build_dashboard(spec, rows, as_of=day, chart_years=0)["price_return"]
    assert all_history["start_date"] == date(2010, 1, 4)
    assert (
        build_dashboard(spec, rows, as_of=date(2010, 1, 4))["price_return"]["annualized_return_pct"]
        is None
    )
