from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from index_valuation.domain import DataError, IndexSpec
from index_valuation.panic_lines import enrich_panic_lines

SPEC = IndexSpec("test", "Test", "000300", "SH.000300", band_min_observations=2)


def point(day, pe="10", **extra):
    return {
        "trade_date": day,
        "pe": Decimal(pe) if pe is not None else None,
        "implied_eps": Decimal("200"),
        "pe_source": "futu",
        "pe_basis": "futu_valuation_pe",
        "pe_usable": True,
        "phase": "published",
        **extra,
    }


def history():
    return [
        point(date(2019, 1, 1), "10"),
        point(date(2020, 1, 1), "20"),
        point(date(2024, 1, 1), "30"),
    ]


def test_past_only_decimal_threshold_and_inputs_remain_unchanged():
    rows = history()
    original = deepcopy(rows)
    result = enrich_panic_lines(SPEC, rows)
    line = result[-1]["panic_line"]
    assert rows == original
    assert all(a is not b for a, b in zip(rows, result, strict=True))
    assert line["version"] == "rolling_pe_p10_v1"
    assert line["as_of"] == date(2024, 1, 1)
    assert line["method"] == "futu_index_implied_v1"
    assert line["threshold_pe"] == Decimal("11")
    assert line["threshold_to_mean"] == Decimal(11) / 15
    assert line["reference_points"] == Decimal("2200")
    assert line["gap_pct"] == (Decimal(30) / 11 - 1) * 100
    assert line["history_count"] == 2
    assert line["history_start"] == date(2019, 1, 1)
    assert line["history_end"] == date(2020, 1, 1)
    assert line["status"] == "reference"
    assert line["history_complete"] is True
    assert line["history_frequency"] == "daily"
    assert line["history_years"] == 5
    assert line["quantile"] == Decimal("0.1")

    revised = rows[:-1] + [point(rows[-1]["trade_date"], "999")]
    future = enrich_panic_lines(SPEC, revised + [point(date(2025, 1, 1), "9999")])
    assert future[-2]["panic_line"]["threshold_pe"] == line["threshold_pe"]
    assert future[-2]["panic_line"]["history_count"] == line["history_count"]


def test_window_boundary_is_inclusive_and_full_span_is_required():
    rows = [point(date(2018, 12, 31), "999")] + history()
    line = enrich_panic_lines(SPEC, rows)[-1]["panic_line"]
    assert line["threshold_pe"] == Decimal("11")
    assert line["history_count"] == 2
    short = history()
    short[-1] = point(date(2023, 12, 31))
    line = enrich_panic_lines(SPEC, short)[-1]["panic_line"]
    assert line["status"] == "insufficient_history"
    assert line["history_complete"] is False
    assert line["threshold_pe"] is None


def test_backcast_and_prelaunch_rows_cannot_supply_five_year_warmup():
    rows = history()
    rows[0]["phase"] = "backcast"
    line = enrich_panic_lines(SPEC, rows)[-1]["panic_line"]
    assert line["history_count"] == 1
    assert line["history_complete"] is False
    spec = replace(SPEC, inception_date=date(2020, 1, 1))
    line = enrich_panic_lines(spec, history())[-1]["panic_line"]
    assert line["history_count"] == 1
    assert line["threshold_pe"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"pe_source": "yahooetf"},
        {"pe_source": None},
        {"pe_basis": "unrelated_pe"},
        {"pe_basis": None},
        {"method": "old_method"},
        {"pe_usable": False},
        {"phase": "backcast"},
    ],
)
def test_wrong_identity_and_unusable_history_never_enter_distribution(extra):
    rows = history()
    rows.insert(2, point(date(2022, 1, 1), "9999", **extra))
    assert enrich_panic_lines(SPEC, rows)[-1]["panic_line"]["threshold_pe"] == Decimal("11")


@pytest.mark.parametrize("pe", [None, "0", "-1", "NaN", "Infinity", "-Infinity"])
def test_invalid_historical_values_are_excluded_without_affecting_p10(pe):
    rows = history()
    rows.insert(2, point(date(2022, 1, 1), pe))
    line = enrich_panic_lines(SPEC, rows)[-1]["panic_line"]
    assert line["threshold_pe"] == Decimal("11")
    assert line["history_count"] == 2


@pytest.mark.parametrize("pe", [None, "0", "-1", "NaN", "Infinity", "-Infinity"])
def test_missing_or_invalid_current_pe_keeps_history_threshold_without_comparison(pe):
    rows = history()
    rows[-1] = point(rows[-1]["trade_date"], pe)
    line = enrich_panic_lines(SPEC, rows)[-1]["panic_line"]
    assert line["status"] == "missing_pe"
    assert line["threshold_pe"] == Decimal("11")
    assert line["gap_pct"] is None
    assert line["reference_points"] is None


@pytest.mark.parametrize(
    ("extra", "status"),
    [
        ({"pe_source": None}, "different_basis"),
        ({"pe_basis": "other"}, "different_basis"),
        ({"method": "other"}, "different_method"),
        ({"pe_usable": False}, "unusable_pe"),
    ],
)
def test_current_source_or_quality_mismatch_disables_comparison(extra, status):
    rows = history()
    rows[-1] = point(rows[-1]["trade_date"], **extra)
    line = enrich_panic_lines(SPEC, rows)[-1]["panic_line"]
    assert line["status"] == status
    assert line["threshold_pe"] == Decimal("11")
    assert line["gap_pct"] is None
    assert line["reference_points"] is None


@pytest.mark.parametrize("eps", [None, Decimal(0), Decimal(-1), Decimal("NaN")])
def test_missing_or_unqualified_eps_only_prevents_price_chart_line(eps):
    rows = history()
    rows[-1]["implied_eps"] = eps
    line = enrich_panic_lines(SPEC, rows)[-1]["panic_line"]
    assert line["status"] == "reference"
    assert line["threshold_pe"] == Decimal("11")
    assert line["gap_pct"] is not None
    assert line["reference_points"] is None


def test_no_threshold_before_minimum_count_or_during_paused_collection():
    line = enrich_panic_lines(replace(SPEC, band_min_observations=252), history())[-1]["panic_line"]
    assert line["status"] == "insufficient_history"
    assert line["minimum_history"] == 252
    assert line["threshold_pe"] is None
    lines = enrich_panic_lines(replace(SPEC, collection_enabled=False), history())
    assert all(p["panic_line"]["status"] == "collection_paused" for p in lines)
    assert all(p["panic_line"]["threshold_pe"] is None for p in lines)
    assert lines[-1]["panic_line"]["reference_points"] is None
    assert lines[-1]["panic_line"]["gap_pct"] is None


def test_weekly_sampling_uses_last_usable_observation_of_completed_weeks():
    spec = replace(SPEC, valuation_source="danjuan")
    source = {"pe_source": "danjuan", "pe_basis": "danjuan_published_pe_ttm"}
    rows = [
        point(date(2019, 1, 1), "100", **source),
        point(date(2024, 1, 1), "10", **source),
        point(date(2024, 1, 4), "20", **source),
        point(date(2024, 1, 5), "999", pe_usable=False, **source),
        point(date(2024, 1, 8), "30", **source),
        point(date(2024, 1, 11), "40", **source),
        point(date(2024, 1, 15), "100", **source),
        point(date(2024, 1, 16), "200", **source),
    ]
    line = enrich_panic_lines(spec, rows)[-1]["panic_line"]
    assert line["history_frequency"] == "weekly"
    assert line["history_count"] == 2
    assert line["history_start"] == date(2024, 1, 4)
    assert line["history_end"] == date(2024, 1, 11)
    assert line["threshold_pe"] == Decimal("22")


def test_futu_rest_uses_index_pe_and_does_not_mix_old_proxy_values():
    spec = replace(SPEC, valuation_source="futorest")
    source = {"pe_source": "futorest", "pe_basis": "futorest_valuation_pe"}
    rows = [point(date(2019, 1, 1), "10", **source)]
    rows += [point(date(2020, 1, 1), "20", **source)]
    rows += [point(date(2022, 1, 1), "999", pe_source="yahooetf")]
    rows += [point(date(2024, 1, 1), "30", **source)]
    line = enrich_panic_lines(spec, rows)[-1]["panic_line"]
    assert line["method"] == "futorest_index_implied_daily_v1"
    assert line["source"] == "futorest"
    assert line["threshold_pe"] == Decimal("11")


def test_unsorted_dates_are_sorted_and_duplicates_rejected():
    rows = history()
    result = enrich_panic_lines(SPEC, list(reversed(rows)))
    assert [p["trade_date"] for p in result] == [p["trade_date"] for p in rows]
    assert enrich_panic_lines(SPEC, []) == []
    with pytest.raises(DataError, match="Duplicate"):
        enrich_panic_lines(SPEC, rows + [rows[-1]])
