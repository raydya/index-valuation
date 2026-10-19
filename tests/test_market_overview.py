from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from index_valuation.domain import IndexSpec
from index_valuation.market_overview import build_market_overview
from index_valuation.panic_lines import enrich_panic_lines
from index_valuation.webapp import create_app

SPEC = IndexSpec("test", "Test", "000300", "SH.000300", band_min_observations=2)


def rows(pe="15", **extra):
    result = [
        {
            "trade_date": day,
            "price": Decimal("1000"),
            "pe": Decimal(value) if value is not None else None,
            "implied_eps": Decimal("50"),
            "pe_source": "futu",
            "pe_basis": "futu_valuation_pe",
            "pe_usable": True,
            "phase": "published",
        }
        for day, value in (
            (date(2019, 1, 1), "10"),
            (date(2020, 1, 1), "20"),
            (date(2024, 1, 1), pe),
        )
    ]
    result[-1].update(extra)
    return result


@pytest.mark.parametrize(
    ("pe", "signal", "label", "action"),
    [
        ("10.99999999999999999999999999", "panic", "恐慌", "持续买入"),
        ("11", "panic", "恐慌", "持续买入"),
        ("11.00000000000000000000000001", "normal", "普通", "继续持有"),
        ("18.99999999999999999999999999", "normal", "普通", "继续持有"),
        ("19", "greed", "贪婪", "减仓离场"),
        ("20", "greed", "贪婪", "减仓离场"),
    ],
)
def test_signals_use_decimal_inclusive_boundaries(pe, signal, label, action):
    point = enrich_panic_lines(SPEC, rows(pe))[-1]
    assert point["valuation_signal"]["signal"] == signal
    overview = build_market_overview({SPEC.key: SPEC}, {SPEC.key: point})
    item = overview["items"][0]
    assert (item["signal"], item["label"], item["action"]) == (signal, label, action)
    assert item["lower_pe"] == Decimal("11")
    assert item["upper_pe"] == Decimal("19")
    assert item["history_end"] == date(2020, 1, 1)
    assert item["history_count"] == 2
    assert item["method"] == "futu_index_implied_v1"


@pytest.mark.parametrize(
    ("extra", "status"),
    [
        ({"pe": None}, "missing_pe"),
        ({"pe": Decimal(0)}, "missing_pe"),
        ({"pe": Decimal(-1)}, "missing_pe"),
        ({"pe": Decimal("NaN")}, "missing_pe"),
        ({"pe_source": "other"}, "different_basis"),
        ({"pe_basis": "other"}, "different_basis"),
        ({"method": "other"}, "different_method"),
        ({"pe_usable": False}, "unusable_pe"),
        ({"phase": "backcast"}, "unusable_pe"),
    ],
)
def test_latest_unusable_data_has_no_signal_and_never_reuses_previous_pe(extra, status):
    point = enrich_panic_lines(SPEC, rows(**extra))[-1]
    assert point["valuation_signal"]["signal"] == "unknown"
    item = build_market_overview({SPEC.key: SPEC}, {SPEC.key: point})["items"][0]
    assert item["signal"] == "unknown"
    # Invalid numerics are never materialized in production. Their canonical PE
    # differs from the input, so an untrusted mismatching detail is also rejected.
    assert item["status"] in {status, "not_calculated"}
    assert item["action"] is None
    assert item["as_of"] == date(2024, 1, 1)


def test_missing_eps_does_not_hide_valid_pe_signal_and_short_history_stays_unknown():
    point = enrich_panic_lines(SPEC, rows("20", implied_eps=None))[-1]
    assert point["valuation_signal"]["signal"] == "greed"
    assert point["greed_line"]["reference_points"] is None
    assert point["panic_line"]["reference_points"] is None
    assert point["greed_line"]["threshold_pe"] == Decimal(19)
    for spec, history in (
        (replace(SPEC, band_min_observations=3), rows()),
        (SPEC, rows(trade_date=date(2023, 12, 31))),
        (replace(SPEC, inception_date=date(2020, 1, 1)), rows()),
    ):
        point = enrich_panic_lines(spec, history)[-1]
        assert point["valuation_signal"]["signal"] == "unknown"
        assert point["valuation_signal"]["status"] == "insufficient_history"
        assert point["greed_line"]["threshold_pe"] is None


def test_constant_distribution_does_not_trigger_both_lights():
    history = rows("10")
    history[1]["pe"] = Decimal(10)
    point = enrich_panic_lines(SPEC, history)[-1]
    assert point["panic_line"]["threshold_pe"] == point["greed_line"]["threshold_pe"]
    assert point["valuation_signal"]["signal"] == "unknown"
    item = build_market_overview({SPEC.key: SPEC}, {SPEC.key: point})["items"][0]
    assert item["status"] == "flat_distribution"
    assert item["action"] is None


def test_paused_unrebuilt_missing_and_old_details_are_explicitly_unknown():
    point = enrich_panic_lines(SPEC, rows("10"))[-1]
    original = deepcopy(point)
    paused = replace(SPEC, collection_enabled=False, collection_disabled_reason="Provider paused")
    item = build_market_overview({SPEC.key: paused}, {SPEC.key: point})["items"][0]
    assert item["signal"] == "unknown"
    assert item["reason"] == "Provider paused"
    assert item["lower_pe"] is item["upper_pe"] is None
    assert point == original
    missing = build_market_overview({SPEC.key: SPEC}, {})["items"][0]
    assert missing["status"] == "missing_data" and missing["as_of"] is None
    point.pop("valuation_signal")
    item = build_market_overview({SPEC.key: SPEC}, {SPEC.key: point})["items"][0]
    assert item["status"] == "not_calculated" and item["signal"] == "unknown"
    for field, value in (("as_of", date(2020, 1, 1)), ("method", "old"), ("version", "old")):
        point = deepcopy(original)
        point["valuation_signal"][field] = value
        item = build_market_overview({SPEC.key: SPEC}, {SPEC.key: point})["items"][0]
        assert item["status"] == "not_calculated"


def test_changed_minimum_history_or_input_quality_invalidates_old_persisted_signal():
    point = enrich_panic_lines(SPEC, rows("10"))[-1]
    changed = replace(SPEC, band_min_observations=252)
    item = build_market_overview({SPEC.key: changed}, {SPEC.key: point})["items"][0]
    assert item["signal"] == "unknown"
    assert item["status"] == "not_calculated"
    for field, value in (
        ("pe_source", "other"),
        ("pe_basis", "other"),
        ("pe_usable", False),
        ("phase", "backcast"),
    ):
        revised = {**point, field: value}
        item = build_market_overview({SPEC.key: SPEC}, {SPEC.key: revised})["items"][0]
        assert item["signal"] == "unknown" and item["status"] == "not_calculated"


def test_current_endpoint_ignores_historical_selection_and_reads_new_values_without_restart():
    class MemoryRepository:
        point = enrich_panic_lines(SPEC, rows("20"))[-1]

        def latest_index_points(self, specs):
            return {SPEC.key: self.point}

    repository = MemoryRepository()
    with TestClient(create_app(repository=repository, specs={SPEC.key: SPEC})) as client:
        first = client.get("/api/market-overview?as_of=2020-01-01").json()
        assert first["items"][0]["as_of"] == "2024-01-01"
        assert first["items"][0]["signal"] == "greed"
        assert first["items"][0]["lower_pe"] == "11.0"
        assert first["items"][0]["upper_pe"] == "19.0"
        assert first["rule"]["upper_quantile"] == "0.9"
        repository.point = enrich_panic_lines(SPEC, rows(None))[-1]
        second = client.get("/api/market-overview").json()
        assert second["items"][0]["signal"] == "unknown"
        assert second["items"][0]["status"] == "missing_pe"
        assert second["items"][0]["pe"] is None
