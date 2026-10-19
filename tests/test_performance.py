import csv
from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest

from index_valuation.domain import TotalReturnSpec
from index_valuation.index_series import build_dashboard, series_csv
from index_valuation.performance import price_return_summary, total_return_summary
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


def test_authoritative_price_allows_return_while_retaining_material_difference():
    rows = [
        point("2020-01-01", "100", price_status="error", price_usable=True),
        point("2024-01-01", "146.41"),
    ]
    result = price_return_summary(rows, date(2020, 1, 1), date(2024, 1, 1))
    assert result["status"] == "ok" and result["price_status"] == "error"
    assert abs(result["annualized_return_pct"] - 10) < Decimal("1e-25")


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


def total_return_spec(**changes):
    return SimpleNamespace(
        **{
            "collection_enabled": True,
            "collection_disabled_reason": "",
            "total_return_unavailable_reason": "",
            "total_return": SimpleNamespace(
                code="H00922",
                name="中证红利全收益指数",
                source="csindex",
                basis="gross_total_return",
                identity_url="https://www.csindex.com.cn/",
            ),
            **changes,
        }
    )


def test_dividend_reinvestment_annualizes_its_own_index_not_price_or_dividend_yield():
    start, end = date(2020, 1, 1), date(2024, 1, 1)
    prices = [point("2020-01-01", "100"), point("2024-01-01", "146.41")]
    total = [point("2020-01-01", "1000"), point("2024-01-01", "1749.00625")]
    price_summary = price_return_summary(prices, start, end)
    result = total_return_summary(total_return_spec(), total, start, end, price_summary)
    assert abs(result["annualized_return_pct"] - Decimal("15")) < Decimal("1e-25")
    assert abs(price_summary["annualized_return_pct"] - Decimal("10")) < Decimal("1e-25")
    assert result["basis"] == "gross_total_return"
    assert result["index_code"] == "H00922"
    assert result["aligned_with_price_return"] is True
    assert result["starts_late"] is False and result["ends_early"] is False
    assert result["reason"] == ""


def test_total_return_excludes_future_and_reports_actual_partial_endpoints():
    start, end = date(2020, 1, 1), date(2024, 1, 1)
    price_summary = {"start_date": start, "end_date": end}
    points = [
        point("2019-12-31", "900"),
        point("2021-01-01", "1000", price_status="unverified", phase="backcast"),
        point("2023-12-29", "1200"),
        point("2024-01-02", "99999"),
    ]
    result = total_return_summary(total_return_spec(), points, start, end, price_summary)
    assert result["status"] == "ok"
    assert result["start_date"] == date(2021, 1, 1)
    assert result["end_date"] == result["latest_available_date"] == date(2023, 12, 29)
    assert result["observation_count"] == 2
    assert result["starts_late"] and result["ends_early"]
    assert result["coverage_status"] == "partial"
    assert result["aligned_with_price_return"] is False
    assert result["requested_start"] == start and result["requested_end"] == end
    assert "历史较短" in result["reason"]
    assert "尚未覆盖" in result["reason"]
    assert "起止日期不同" in result["reason"]
    assert "单源" in result["reason"]
    assert "回溯" in result["reason"]
    assert result == total_return_summary(
        total_return_spec(), points[:-1], start, end, price_summary
    )


@pytest.mark.parametrize("points", [[], [point("2024-01-01", None)], [point("2024-01-01", "100")]])
def test_total_return_missing_or_single_point_is_not_zero(points):
    result = total_return_summary(
        total_return_spec(), points, date(2020, 1, 1), date(2024, 1, 1), {}
    )
    assert result["status"] == "insufficient_data"
    assert result["annualized_return_pct"] is None
    assert result["reason"]


@pytest.mark.parametrize(
    "bad_point,expected",
    [
        (point("2020-01-01", "0"), "invalid_price"),
        (point("2020-01-01", "NaN"), "invalid_price"),
        (point("2020-01-01", "100", price_status="error"), "price_discrepancy"),
        (
            point("2020-01-01", "100", price_status="error", price_usable=True),
            "price_discrepancy",
        ),
    ],
)
def test_total_return_bad_endpoint_is_not_skipped_or_overridden(bad_point, expected):
    points = [bad_point, point("2021-01-01", "110"), point("2024-01-01", "150")]
    result = total_return_summary(
        total_return_spec(), points, date(2020, 1, 1), date(2024, 1, 1), {}
    )
    assert result["status"] == expected
    assert result["annualized_return_pct"] is None
    assert result["start_date"] == date(2020, 1, 1)
    assert result["reason"]


@pytest.mark.parametrize(
    "changes,status,reason",
    [
        (
            {"total_return": None, "total_return_unavailable_reason": "尚无有效映射"},
            "not_configured",
            "尚无有效映射",
        ),
        (
            {"collection_enabled": False, "collection_disabled_reason": "暂停数据采集"},
            "collection_paused",
            "暂停数据采集",
        ),
    ],
)
def test_total_return_no_mapping_or_paused_does_not_reuse_old_prices(changes, status, reason):
    points = [point("2020-01-01", "100"), point("2024-01-01", "150")]
    result = total_return_summary(
        total_return_spec(**changes), points, date(2020, 1, 1), date(2024, 1, 1), {}
    )
    assert result["status"] == status and result["reason"] == reason
    assert result["annualized_return_pct"] is None


def test_total_return_short_duration_warns_and_does_not_require_pe_samples():
    points = [point("2024-01-01", "100"), point("2024-02-01", "105")]
    start, end = date(2024, 1, 1), date(2024, 2, 1)
    result = total_return_summary(
        total_return_spec(), points, start, end, {"start_date": start, "end_date": end}
    )
    assert result["status"] == "ok"
    assert result["elapsed_days"] == 31
    assert "不足1年" in result["reason"]


def test_total_return_old_history_does_not_fill_empty_selected_window():
    points = [point("2019-01-01", "100"), point("2019-02-01", "105")]
    result = total_return_summary(
        total_return_spec(), points, date(2020, 1, 1), date(2024, 1, 1), {}
    )
    assert result["status"] == "insufficient_data"
    assert result["latest_available_date"] == date(2019, 2, 1)
    assert result["start_date"] is None and result["end_date"] is None
    assert result["annualized_return_pct"] is None


@pytest.mark.parametrize(
    "chart_years,expected_start",
    [(0, "2015-01-05"), (10, "2015-01-05"), (5, "2015-01-05"), (1, "2019-01-04")],
)
def test_longer_total_return_history_is_bounded_by_visible_price_range_and_as_of(
    chart_years, expected_start
):
    spec = replace(
        load_indices()["csi300"],
        total_return=TotalReturnSpec(
            code="H00300",
            name="沪深300全收益指数",
            source="csindex",
            symbol="H00300",
            history_start_date=date(2004, 12, 31),
            identity_url="https://www.csindex.com.cn/",
        ),
    )
    prices = [
        point("2015-01-05", "100"),
        point("2019-01-04", "130"),
        point("2020-01-04", "140"),
        point("2025-01-06", "99999"),
    ]
    total = [
        point("2004-12-31", "5"),
        point("2010-01-04", "500"),
        point("2015-01-05", "1000"),
        point("2019-01-04", "1500"),
        point("2020-01-04", "1700"),
        point("2025-01-06", "99999"),
    ]
    dashboard = build_dashboard(
        spec,
        prices,
        total_return_points=total,
        chart_years=chart_years,
        as_of=date(2020, 1, 4),
    )
    summary = dashboard["total_return"]
    assert summary["start_date"] == date.fromisoformat(expected_start)
    assert summary["requested_start"] == summary["start_date"]
    assert summary["end_date"] == summary["requested_end"] == date(2020, 1, 4)
    assert summary["aligned_with_price_return"] is True
    assert summary["latest_available_date"] == date(2020, 1, 4)
    assert summary["end_price"] == 1700
    exported = {
        row["trade_date"]: row
        for row in csv.DictReader(StringIO(series_csv(dashboard).lstrip("\ufeff")))
    }
    for endpoint in ("start", "end"):
        row = exported[str(summary[f"{endpoint}_date"])]
        assert Decimal(row["total_return_level"]) == summary[f"{endpoint}_price"]
    assert (
        summary
        == build_dashboard(
            spec,
            prices,
            total_return_points=total,
            history_years=10,
            chart_years=chart_years,
            as_of=date(2020, 1, 4),
        )["total_return"]
    )
