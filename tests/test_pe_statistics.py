"""Frozen pre-optimization outputs and shared PE statistics regression cases."""

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random

import pytest

from index_valuation.archive import json_text
from index_valuation.domain import IndexSpec, TotalReturnSpec
from index_valuation.index_series import build_dashboard, series_csv, valuation_profile

BASELINE = Path(__file__).parent / "fixtures" / "pe_statistics_before_optimization.json"
CASES = {
    "daily_five_year_leap_boundary": ("futu", date(2024, 2, 29), 5, 5, False),
    "weekly_five_year_leap_boundary": ("danjuan", date(2024, 2, 29), 5, 5, False),
    "daily_all_history": ("futu", None, 0, 0, False),
    "weekly_weekend_short_chart": ("danjuan", date(2024, 1, 7), 1, 3, False),
    "short_history": ("futu", None, 5, 5, True),
}


def regression_inputs(name):
    source, as_of, chart_years, history_years, short = CASES[name]
    spec = IndexSpec(
        "statistics",
        "统计回归指数",
        "000300",
        "SH.000300",
        band_min_observations=2,
        valuation_source=source,
        inception_date=date(2024, 1, 1) if short else date(2005, 4, 8),
        total_return=TotalReturnSpec(
            "RETURN", "全收益测试", "csindex", "RETURN", date(2010, 1, 1), "https://example.test"
        ),
    )
    samples = [
        ("2010-01-01", "1000", True),
        ("2019-02-27", "2", True),
        ("2019-02-28", "10.1234567890123456", "missing"),
        ("2019-03-01", "12.1234567890123456", True),
        ("2020-02-28", "20", True),
        ("2023-12-25", "30", True),
        ("2023-12-29", "20", True),
        ("2024-01-02", None, True),
        ("2024-01-03", "0", True),
        ("2024-01-04", "20", True),
        ("2024-02-26", "999", False),
        ("2024-02-27", "777", None),
        ("2024-02-28", "-2", True),
        ("2024-02-29", "20", True),
        ("2024-03-01", "9999", True),
    ]
    if short:
        samples = [
            ("2024-01-02", "10", True),
            ("2024-01-03", "20", True),
            ("2024-01-04", "15", True),
        ]
    profile = valuation_profile(spec)
    points = []
    for i, (day, value, usable) in enumerate(samples):
        pe = Decimal(value) if value is not None else None
        points.append(
            {
                "trade_date": date.fromisoformat(day),
                "price": Decimal(1000 + i) if i != 2 else None,
                "pe": pe,
                "implied_eps": Decimal(1000 + i) / pe if pe and i != 2 else None,
                "price_source": "futu",
                "price_status": "pass",
                "pe_source": profile["source"],
                "pe_basis": profile["basis"],
                "pe_comparison_status": "unconfirmed",
                **({"pe_usable": usable} if usable != "missing" else {}),
                "phase": "backcast" if day < spec.inception_date.isoformat() else "published",
            }
        )
    returns = [
        {
            "trade_date": point["trade_date"],
            "price": Decimal(2000 + i * 2),
            "price_source": "csindex",
            "price_status": "pass",
        }
        for i, point in enumerate(points)
    ]
    return (
        spec,
        points,
        {
            "as_of": as_of,
            "chart_years": chart_years,
            "history_years": history_years,
            "total_return_points": returns,
        },
    )


@pytest.mark.parametrize("name", CASES)
def test_dashboard_and_csv_match_frozen_pre_optimization_outputs(name):
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))[name]
    spec, points, options = regression_inputs(name)
    dashboard = build_dashboard(spec, points, **options)
    assert json.loads(json_text(dashboard)) == expected["dashboard"]
    assert series_csv(dashboard) == expected["csv"]


@pytest.mark.parametrize("name", CASES)
def test_latest_rank_matches_frozen_dashboard_using_only_pe_inputs(name):
    from index_valuation.index_series import latest_pe_statistics

    expected = json.loads(BASELINE.read_text(encoding="utf-8"))[name]["dashboard"]["selected"]
    spec, points, options = regression_inputs(name)
    eligible = [
        {key: point[key] for key in ("trade_date", "pe", "pe_usable") if key in point}
        for point in reversed(points)
        if options["as_of"] is None or point["trade_date"] <= options["as_of"]
    ]
    actual = latest_pe_statistics(spec, eligible, history_years=options["history_years"])
    actual = json.loads(json_text(actual))
    assert actual == {
        "trade_date": expected["trade_date"],
        "pe": expected["pe"],
        "pe_usable": expected.get("pe_usable", True),
        "percentile": expected["percentile"],
        "history_count": expected["history_count"],
    }


@pytest.mark.parametrize(
    ("pe", "usable"), [(None, True), (Decimal(0), True), (Decimal(-1), True), (Decimal(15), False)]
)
def test_latest_rank_preserves_missing_or_unusable_current_pe(pe, usable):
    from index_valuation.index_series import latest_pe_statistics

    spec, points, _ = regression_inputs("short_history")
    points[-1].update(pe=pe, pe_usable=usable)
    result = latest_pe_statistics(spec, points)
    assert result["history_count"] == 2
    assert result["percentile"] is None
    assert result["pe"] == pe


def test_latest_rank_does_not_construct_dashboard_bands_or_return_summaries(monkeypatch):
    from index_valuation.index_series import latest_pe_statistics

    def unexpected_full_calculation(*args, **kwargs):
        raise AssertionError("Latest rank must not construct a dashboard, bands or returns")

    for target in ("build_dashboard", "price_return_summary", "total_return_summary"):
        monkeypatch.setattr(f"index_valuation.index_series.{target}", unexpected_full_calculation)
    monkeypatch.setattr(
        "index_valuation.pe_statistics.quantile_sorted", unexpected_full_calculation
    )
    spec, points, _ = regression_inputs("short_history")
    assert latest_pe_statistics(spec, points)["percentile"] == Decimal(50)
    assert latest_pe_statistics(spec, []) == {}


@pytest.mark.parametrize("weekly", [False, True])
@pytest.mark.parametrize("years", [0, 3, 5, 10])
@pytest.mark.parametrize("minimum", [2, 20, 1000])
def test_latest_rank_matches_independent_sample_selection(weekly, years, minimum):
    from index_valuation.index_series import latest_pe_statistics, years_before

    spec, _, _ = regression_inputs("short_history")
    spec = replace(
        spec, valuation_source="danjuan" if weekly else "futu", band_min_observations=minimum
    )
    random = Random(3842)
    rows = []
    day = date(2010, 1, 1)
    for _ in range(420):
        day += timedelta(days=random.randint(1, 25))
        value = random.choice([None, "0", "-2", "10.1234567890123456", "20", "20", "30"])
        rows.append(
            {
                "trade_date": day,
                "pe": Decimal(value) if value is not None else None,
                "pe_usable": random.choice([True, True, True, False, None]),
            }
        )
    for length in (6, 150, 420):
        selected = rows[length - 1]
        lower = years_before(selected["trade_date"], years)
        samples = [
            row
            for row in rows[: length - 1]
            if row["trade_date"] >= lower
            and row["pe"] is not None
            and row["pe"] > 0
            and row["pe_usable"]
        ]
        if weekly:
            completed_weeks = {
                row["trade_date"].isocalendar()[:2]: row
                for row in samples
                if row["trade_date"].isocalendar()[:2] < selected["trade_date"].isocalendar()[:2]
            }
            samples = list(completed_weeks.values())
        expected = None
        pe = selected["pe"]
        if len(samples) >= minimum and pe is not None and pe > 0 and selected["pe_usable"]:
            expected = (
                Decimal(
                    sum(row["pe"] < pe for row in samples) + sum(row["pe"] <= pe for row in samples)
                )
                / 2
                / len(samples)
                * 100
            )
        result = latest_pe_statistics(spec, rows[:length], history_years=years)
        assert result["history_count"] == len(samples)
        assert result["percentile"] == expected
