from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from index_valuation.index_series import method_for
from index_valuation.panic_reference import load_panic_reference, panic_reference_for
from index_valuation.registry import load_indices
from index_valuation.webapp import create_app

SPECS = load_indices(Path(__file__).resolve().parents[1] / "indices.toml")
STUDY = load_panic_reference()


def dashboard(key="csi300", day=date(2026, 10, 15), **values):
    record = STUDY["indices"][key]
    return {
        "index": {"key": key},
        "method": method_for(SPECS[key]),
        "requested_date": None,
        "selected": {
            "trade_date": day,
            "pe": Decimal("21.808"),
            "pe_source": record["source"],
            "pe_basis": record["basis"],
            "pe_usable": True,
            **values,
        },
    }


def test_dated_research_keeps_thresholds_and_overlapping_sample_counts_separate():
    expected = {
        "csi300": ("10.904", 18, 3),
        "sse": ("12.298", 35, 3),
        "chinext": ("28.279", 10, 2),
        "star50": ("41.371", 0, 0),
        "hstech": ("20.4292", 0, 0),
        "divlowvol": ("5.08", 1, 1),
        "sp500": ("20.8124", 2, 1),
        "nasdaq100": ("26.0971", 2, 1),
    }
    assert STUDY["study_date"] == "2026-10-14"
    assert set(expected) | {"csia500", "ustotal"} <= set(STUDY["indices"])
    for key, (threshold, months, windows) in expected.items():
        record = STUDY["indices"][key]
        assert Decimal(record["threshold_pe"]) == Decimal(threshold)
        assert record["three_year"]["n"] == months
        assert record["three_year"]["nonoverlap"]["n"] == windows
        assert len(record["three_year"]["nonoverlap_samples"]) == windows
    # A historical losing window must stay visible alongside a positive median.
    csi = STUDY["indices"]["csi300"]["three_year"]
    assert Decimal(csi["nonoverlap_samples"][0]["annualized_return"]) < 0
    assert Decimal(csi["median_annualized_return"]) > 0
    alternate = STUDY["indices"]["star50"]["reference_thresholds"][0]
    assert alternate["source"] == "csindex"
    assert Decimal(alternate["threshold_pe"]) == Decimal("28.07")


@pytest.mark.parametrize(
    "key,status",
    [
        ("csia500", "insufficient_history"),
        ("ustotal", "missing_pe"),
    ],
)
def test_short_history_and_missing_pe_never_receive_a_threshold(key, status):
    result = panic_reference_for(dashboard(key, pe=None), STUDY, SPECS)
    assert result["record"]["threshold_pe"] is None
    assert result["record"]["threshold_to_mean"] is None
    assert result["record"]["three_year"]["median_annualized_return"] is None
    assert result["comparison"]["status"] == status
    assert result["comparison"]["gap_pct"] is None
    assert result["comparison"]["reference_points"] is None


def test_new_proxy_snapshot_does_not_rewrite_the_old_missing_pe_research():
    data = dashboard(
        "ustotal",
        pe=Decimal("24.300972"),
        pe_source="yahooetf",
        pe_basis="yahoo_etf_vti_trailing_pe_proxy",
    )
    result = panic_reference_for(data, STUDY, SPECS)
    assert result["record"]["status"] == "missing_pe"
    assert result["comparison"]["status"] == "insufficient_history"
    assert result["comparison"]["reference_points"] is None


def test_gap_is_selected_pe_relative_to_fixed_threshold_and_does_not_mutate_record():
    frozen = deepcopy(STUDY)
    for pe, gap in (("21.808", 100), ("5.452", -50), ("10.904", 0)):
        result = panic_reference_for(dashboard(pe=Decimal(pe)), STUDY, SPECS)
        assert result["comparison"]["status"] == "available"
        assert result["comparison"]["gap_pct"] == gap
        assert result["comparison"]["date"] == date(2026, 10, 15)
    assert STUDY == frozen


def test_horizontal_reference_uses_only_selected_eps_and_preserves_decimal_precision():
    selected = dashboard(implied_eps=Decimal("200.123456789"))
    # A past row must not affect the horizontal line's anchor.
    selected["series"] = [{"implied_eps": Decimal("99999")}]
    result = panic_reference_for(selected, STUDY, SPECS)
    assert result["comparison"]["reference_points"] == Decimal("2182.146172827256")
    selected["selected"]["implied_eps"] = Decimal("100")
    assert panic_reference_for(selected, STUDY, SPECS)["comparison"]["reference_points"] == (
        Decimal("1090.4")
    )


@pytest.mark.parametrize("eps", [None, Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_missing_or_invalid_eps_does_not_become_a_price_reference(eps):
    result = panic_reference_for(dashboard(implied_eps=eps), STUDY, SPECS)
    assert result["comparison"]["status"] == "available"
    assert result["comparison"]["reference_points"] is None
    assert result["record"]["threshold_pe"] is not None


@pytest.mark.parametrize(
    "values,status",
    [
        ({"pe": None}, "missing_pe"),
        ({"pe": Decimal("0")}, "missing_pe"),
        ({"pe": Decimal("-3")}, "missing_pe"),
        ({"pe": Decimal("NaN")}, "missing_pe"),
        ({"pe_usable": False}, "unusable_pe"),
        ({"pe_source": "csindex"}, "different_basis"),
        ({"pe_basis": "forward_pe"}, "different_basis"),
    ],
)
def test_gap_never_fills_or_mixes_unusable_and_different_basis_pe(values, status):
    comparison = panic_reference_for(dashboard(implied_eps=Decimal("200"), **values), STUDY, SPECS)[
        "comparison"
    ]
    assert comparison["status"] == status
    assert comparison["gap_pct"] is None
    assert comparison["reference_points"] is None


def test_historical_view_does_not_compare_against_future_research():
    data = dashboard(day=date(2020, 1, 2), implied_eps=Decimal("200"))
    data["requested_date"] = date(2020, 1, 2)
    result = panic_reference_for(data, STUDY, SPECS)
    assert result["comparison"]["status"] == "before_study"
    assert result["comparison"]["gap_pct"] is None
    assert result["comparison"]["reference_points"] is None
    assert result["study_date"] == "2026-10-14"
    # Latest available US data may precede the research date; this is today's
    # numerical comparison, explicitly dated, rather than an as-of backtest.
    result = panic_reference_for(dashboard("sp500", day=date(2026, 10, 13)), STUDY, SPECS)
    assert result["comparison"]["status"] == "available"


def test_official_policy_change_is_flagged_without_rewriting_old_research():
    result = panic_reference_for(dashboard("divlowvol"), STUDY, SPECS)
    assert result["comparison"]["method_changed"] is True
    assert result["comparison"]["status"] == "available"
    assert result["record"]["method"] == "csindex_index_implied_daily_v1"
    assert Decimal(result["record"]["threshold_pe"]) == Decimal("5.08")


def test_new_index_without_research_uses_generic_empty_state():
    spec = replace(SPECS["csi300"], key="new_index", name="New Index")
    data = dashboard()
    data["index"]["key"] = spec.key
    result = panic_reference_for(data, STUDY, {spec.key: spec})
    assert result["record"] is None
    assert result["comparison"]["status"] == "no_record"
    assert result["overview"][0]["name"] == "New Index"
    assert result["overview"][0]["threshold_pe"] is None


def test_api_records_are_window_independent_and_download_matches_dashboard():
    class MemoryRepository:
        def panic_lines(self, specs, as_of=None):
            return {}

        def greed_lines(self, specs, as_of=None):
            return {}

        def total_return_points(self, spec):
            return []

        def index_series(self, key, method):
            return [
                {
                    **dashboard()["selected"],
                    "price": Decimal("4000"),
                    "price_source": "futu",
                    "price_status": "pass",
                    "implied_eps": Decimal("200"),
                    "pe_comparison_status": "missing",
                }
            ]

    with TestClient(create_app(repository=MemoryRepository(), specs=SPECS)) as client:
        responses = [
            client.get(f"/api/indices/csi300/dashboard?chart_years={c}&history_years={h}")
            for c, h in ((1, 3), (5, 5), (0, 10))
        ]
        assert all(r.status_code == 200 for r in responses)
        references = [r.json()["panic_reference"] for r in responses]
        assert references[0] == references[1] == references[2]
        assert Decimal(references[0]["comparison"]["gap_pct"]) == 100
        assert Decimal(references[0]["comparison"]["reference_points"]) == Decimal("2180.8")
        download = client.get("/api/panic-reference")
        assert download.status_code == 200
        assert "attachment" in download.headers["Content-Disposition"]
        assert download.json()["indices"]["csi300"] == references[0]["record"]
