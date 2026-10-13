from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from index_valuation.domain import Constituent, IndexSpec, Observation, WeightSnapshot
from index_valuation.quality import compare_observations, compare_value
from index_valuation.valuation import calculate, quantile

DAY = date(2026, 9, 30)
SPEC = IndexSpec("example", "Example", "000001", "SH.000001", 2)


def example_data():
    snapshot = WeightSnapshot(
        DAY,
        (
            Constituent("SH.600001", "A", D(60)),
            Constituent("SZ.000001", "B", D(40)),
        ),
        "csindex",
        "https://example.test/weights",
        "test",
        "test.xls",
    )
    now = datetime(2026, 9, 30, 13, tzinfo=UTC)
    rows = {
        symbol: Observation(symbol, DAY, "futu", price, D(1), price, now, now)
        for symbol, price in (("SH.600001", D(10)), ("SZ.000001", D(20)))
    }
    return snapshot, rows


def value(snapshot, rows, *, history=None, spec=SPEC, day=DAY, checks=None):
    reference = {symbol: replace(row, source="baostock") for symbol, row in rows.items()}
    checks = checks if checks is not None else compare_observations(list(rows), rows, reference)
    return calculate(spec, day, snapshot, rows, D(100), checks, history or [])


def test_aggregate_earnings_yield_not_arithmetic_average_of_prices_or_pe():
    snapshot, rows = example_data()
    result = value(snapshot, rows)
    assert result.status == "verified"
    assert result.pe_ttm == D("12.5")
    assert result.index_eps_ttm == D(8)
    assert result.bands["fixed_multiples"]["15"] == D(120)
    assert result.bands["historical_quantiles"] is None


def test_losses_remain_in_the_portfolio():
    snapshot, rows = example_data()
    rows["SZ.000001"] = replace(rows["SZ.000001"], eps_ttm=D(-2))
    result = value(snapshot, rows)
    assert result.pe_ttm == D(50)
    rows["SZ.000001"] = replace(rows["SZ.000001"], eps_ttm=D(-4))
    result = value(snapshot, rows)
    assert result.status == "non_positive_earnings"
    assert result.pe_ttm is None and not result.bands


def test_missing_eps_is_not_treated_as_zero_or_renormalized_away():
    snapshot, rows = example_data()
    rows["SZ.000001"] = replace(rows["SZ.000001"], eps_ttm=None)
    result = value(snapshot, rows)
    assert result.status == "blocked"
    assert result.coverage_weight == D("0.6")
    assert result.pe_ttm is None


def test_future_weights_rejected_and_older_weights_explicitly_marked():
    snapshot, rows = example_data()
    assert value(replace(snapshot, as_of=date(2026, 10, 1)), rows).status == "blocked"
    result = value(replace(snapshot, as_of=date(2026, 9, 29)), rows)
    assert result.status == "estimated"
    assert "dated_weights_estimate" in result.issues
    assert value(replace(snapshot, as_of=date(2026, 1, 1)), rows).status == "blocked"


def test_rounded_weight_sum_is_normalized_once():
    snapshot, rows = example_data()
    snapshot = replace(
        snapshot,
        constituents=tuple(
            replace(item, weight_percent=item.weight_percent * D("1.00002"))
            for item in snapshot.constituents
        ),
    )
    assert value(snapshot, rows).pe_ttm == D("12.5")


def test_mixed_dates_and_incomplete_verification_block_calculation():
    snapshot, rows = example_data()
    assert value(snapshot, rows, checks=[]).status == "blocked"
    rows["SZ.000001"] = replace(rows["SZ.000001"], trade_date=date(2026, 9, 29))
    assert value(snapshot, rows).status == "blocked"


def test_independent_discrepancies_are_reported_and_large_ones_blocked():
    snapshot, rows = example_data()
    reference = {symbol: replace(row, source="baostock") for symbol, row in rows.items()}
    reference["SZ.000001"] = replace(reference["SZ.000001"], eps_ttm=D("1.03"))
    checks = compare_observations(list(rows), rows, reference)
    assert value(snapshot, rows, checks=checks).status == "review"
    reference["SZ.000001"] = replace(reference["SZ.000001"], eps_ttm=D("1.06"))
    checks = compare_observations(list(rows), rows, reference)
    assert value(snapshot, rows, checks=checks).status == "blocked"


def test_a_source_cannot_verify_itself():
    snapshot, rows = example_data()
    checks = compare_observations(list(rows), rows, rows)
    assert value(snapshot, rows, checks=checks).status == "blocked"


@pytest.mark.parametrize(
    ("left", "right", "status"),
    [
        ("100", "101", "pass"),
        ("100", "105", "warning"),
        ("100", "106", "error"),
        ("-100", "-106", "error"),
        ("0", "0", "pass"),
        ("0", "1", "error"),
    ],
)
def test_discrepancy_thresholds_and_zero_and_negative_values(left, right, status):
    assert compare_value("x", "eps_ttm", D(left), D(right)).status == status


def test_historical_bands_require_enough_observations():
    snapshot, rows = example_data()
    spec = replace(SPEC, band_min_observations=4)
    assert (
        value(snapshot, rows, history=[D(8), D(10), D(12)], spec=spec).bands["historical_quantiles"]
        is None
    )
    result = value(snapshot, rows, history=[D(8), D(10), D(12), D(14)], spec=spec)
    assert result.bands["historical_quantiles"]["0.5"] == D(11)
    assert result.bands["historical_band_points"]["0.5"] == D(88)
    assert quantile([D(8), D(10), D(12), D(14)], D("0.25")) == D("9.5")
