from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from index_valuation.domain import IndexSpec
from index_valuation.panic_study import (
    anniversary,
    features,
    matches,
    monthly_signals,
    nonoverlapping,
    outcome,
    prepare,
    summarize,
)

SPEC = IndexSpec("test", "Test", "000300", "SH.000300", band_min_observations=2)


def point(day, pe="10", price="100", **extra):
    return {
        "trade_date": day,
        "pe": Decimal(pe) if pe is not None else None,
        "price": Decimal(price),
        "implied_eps": Decimal(10),
        "pe_usable": True,
        "phase": "published",
        "price_status": "pass",
        "pe_comparison_status": "missing",
        "price_source": "futu",
        "pe_source": "futu",
        **extra,
    }


def daily_prices(start, end):
    return [point(start + timedelta(days=i)) for i in range((end - start).days + 1)]


def test_history_excludes_current_future_and_requires_full_elapsed_window():
    rows = [point(date(y, 1, 1), str(y - 2000)) for y in range(2019, 2025)]
    old = features(SPEC, rows, peak_sessions=2)
    revised = rows[:-1] + [point(date(2024, 1, 1), "999")]
    future = features(SPEC, revised + [point(date(2025, 1, 1), "9999")], peak_sessions=2)
    assert old[-2]["history_complete"] is False
    assert old[-1]["history_complete"] is True
    assert old[-1]["mean_pe"] == Decimal(21)
    assert future[-2]["mean_pe"] == old[-1]["mean_pe"]
    assert future[-2]["quantile_pe"] == old[-1]["quantile_pe"]


def test_weekly_history_uses_last_observation_of_completed_weeks_only():
    spec = replace(SPEC, valuation_source="danjuan")
    rows = [
        point(date(2024, 1, 1), "10"),
        point(date(2024, 1, 4), "20"),
        point(date(2024, 1, 8), "30"),
        point(date(2024, 1, 11), "40"),
        point(date(2024, 1, 15), "100"),
        point(date(2024, 1, 16), "200"),
    ]
    data = features(spec, rows, peak_sessions=2)
    assert data[-1]["history_count"] == 2
    assert data[-1]["mean_pe"] == 30
    assert data[-1]["history_end"] == date(2024, 1, 11)


def test_strict_pe_sensitivity_removes_errors_from_history_and_signal():
    rows = [
        point(date(2019, 1, 1), "10"),
        point(date(2020, 1, 1), "1000", pe_comparison_status="error"),
        point(date(2021, 1, 1), "20"),
        point(date(2024, 1, 1), "10"),
    ]
    strict = prepare(rows, exclude_pe_errors=True)
    assert rows[1]["pe_usable"] is True  # no production-policy mutation
    assert strict[1]["pe_usable"] is False
    assert features(SPEC, strict, peak_sessions=2)[-1]["mean_pe"] == 15


def test_prelaunch_pe_cannot_supply_research_warmup():
    rows = [
        point(date(2010, 1, 1), phase="backcast"),
        point(date(2020, 1, 1)),
        point(date(2021, 1, 1)),
        point(date(2024, 1, 1)),
    ]
    data = features(SPEC, prepare(rows), peak_sessions=2)
    assert data[-1]["history_complete"] is False


def test_month_sampling_does_not_pick_later_panic_or_fill_missing_pe():
    rows = [
        {"trade_date": date(2023, 1, 5), "eligible": True, "pe": 20},
        {"trade_date": date(2023, 1, 12), "eligible": True, "pe": 8},
        {"trade_date": date(2023, 2, 1), "eligible": False, "pe": None},
        {"trade_date": date(2023, 3, 1), "eligible": True, "pe": 10},
    ]
    assert monthly_signals(rows, date(2023, 3, 20)) == rows[:1]


def test_entry_uses_next_session_and_reports_observed_loss_separately_from_drawdown():
    prices = daily_prices(date(2020, 1, 1), date(2021, 1, 4))
    prices[0]["price"] = Decimal(1)
    prices[20]["price"] = Decimal(200)
    prices[30]["price"] = Decimal(80)
    prices[-3]["price"] = Decimal(110)  # 2021-01-02: anniversary of next-session entry
    signal = {
        **prices[0],
        "percentile": 5,
        "pe_to_mean": Decimal("0.7"),
        "drawdown_252": Decimal("-0.3"),
    }
    sample, reason = outcome(signal, prices, 1)
    assert reason is None
    assert sample["entry_date"] == date(2020, 1, 2)
    assert sample["exit_date"] == date(2021, 1, 2)
    assert sample["cumulative_return"] == Decimal("0.1")
    assert sample["lowest_return_from_entry"] == Decimal("-0.2")
    assert sample["maximum_close_drawdown"] == Decimal("-0.6")


def test_unverified_endpoint_is_excluded_not_skipped_and_immature_is_not_zero():
    prices = daily_prices(date(2020, 1, 1), date(2021, 1, 4))
    prices[1]["price_status"] = "unverified"
    assert outcome(prices[0], prices, 1) == (None, "unverified_entry")
    prices[1]["price"] = None
    prices[1]["price_status"] = "missing"
    assert outcome(prices[0], prices, 1) == (None, "unverified_entry")
    prices[1]["price"] = Decimal(100)
    prices[1]["price_status"] = "pass"
    prices[-3]["price_status"] = "unverified"
    assert outcome(prices[0], prices, 1) == (None, "unverified_exit")
    assert outcome(prices[0], prices, 3) == (None, "immature")


def test_rule_boundaries_and_nonoverlapping_holdings():
    row = {"percentile": Decimal(10), "drawdown_252": Decimal("-0.2"), "pe_to_mean": Decimal("0.8")}
    assert matches(row, "panic_p10_dd20")
    assert not matches(row, "panic_p05_dd20")
    assert matches(row, "mean80_dd20")
    samples = [
        {"entry_date": date(2020, 1, 2), "exit_date": date(2023, 1, 2)},
        {"entry_date": date(2021, 1, 2), "exit_date": date(2024, 1, 2)},
        {"entry_date": date(2023, 1, 2), "exit_date": date(2026, 1, 2)},
    ]
    assert nonoverlapping(samples) == [samples[0], samples[2]]
    assert summarize([]) == {"n": 0}
    assert anniversary(date(2020, 2, 29), 1) == date(2021, 2, 28)


def test_duplicate_dates_fail_instead_of_reweighting():
    row = point(date(2020, 1, 1))
    with pytest.raises(ValueError, match="Duplicate"):
        features(SPEC, [row, row])
