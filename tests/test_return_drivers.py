"""Reconciliation and missing-data boundaries for the offline research tool."""

import importlib.util
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "analyze_return_drivers",
    Path(__file__).resolve().parents[1] / "scripts/analyze_return_drivers.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
analyze_dashboard = module.analyze_dashboard


def dashboard():
    # Earnings double and PE halves, so price stays flat; dividends add 10%.
    points = []
    for day, pe, earnings, tr in [
        ("2020-01-01", "10", "10", "200"),
        ("2021-01-01", "5", "20", "220"),
    ]:
        points.append(
            {
                "trade_date": day,
                "price": "100",
                "pe": pe,
                "implied_eps": earnings,
                "pe_source": "futu",
                "pe_basis": "futu_valuation_pe",
                "pe_usable": True,
                "price_status": "pass",
                "total_return_level": tr,
                "total_return_status": "pass",
                "total_return_code": "H00300",
                "total_return_basis": "gross_total_return",
                "total_return_source": "csindex",
            }
        )
    with localcontext() as context:
        context.prec = 40
        cagr = (Decimal("1.1") ** (Decimal("365.25") / 366) - 1) * 100
    summary = {"start_date": "2020-01-01", "end_date": "2021-01-01", "status": "ok"}
    return {
        "index": {"key": "test", "name": "Test"},
        "series": points,
        "selected": points[-1],
        "price_return": {**summary, "annualized_return_pct": "0"},
        "total_return": {
            **summary,
            "basis": "gross_total_return",
            "index_code": "H00300",
            "source": "csindex",
            "annualized_return_pct": str(cagr),
        },
    }


def test_geometric_factors_reconcile_and_log_contributions_add():
    result = analyze_dashboard(dashboard())
    split = result["primary_decomposition"]
    assert result["elapsed_days"] == 366
    assert split["implicit_earnings"]["factor"] == 2
    assert split["valuation"]["factor"] == Decimal("0.5")
    assert result["reinvestment_increment"]["factor"] == Decimal("1.1")
    assert abs(split["reconciliation_residual"]) < Decimal("1e-30")
    contribution = sum(
        x["annualized_log_contribution"]
        for x in (
            split["implicit_earnings"],
            split["valuation"],
            result["reinvestment_increment"],
        )
    )
    assert abs(contribution - result["total_return"]["annualized_log_contribution"]) < Decimal(
        "1e-25"
    )


def test_missing_primary_pe_is_not_filled_from_reference_or_later_date():
    data = dashboard()
    data["series"][0].update(pe=None, implied_eps=None)
    for p in data["series"]:
        p.update(reference_pe="10", reference_pe_source="csindex", reference_pe_basis="rolling")
    result = analyze_dashboard(data)
    assert result["primary_decomposition"]["status"] == "missing_endpoint_pe"
    assert result["first_available_primary_pe"] == "2021-01-01"
    assert result["reference_sensitivity"]["status"] == "available"
    assert result["total_return"]["factor"] == Decimal("1.1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("pe_source", "csindex"),
        ("pe_basis", "different"),
        ("pe_usable", False),
        ("pe", "0"),
        ("implied_eps", None),
    ],
)
def test_pe_split_does_not_mix_sources_or_use_ineligible_values(field, value):
    data = dashboard()
    data["series"][-1][field] = value
    result = analyze_dashboard(data)
    assert result["primary_decomposition"]["status"] != "available"


@pytest.mark.parametrize("change", ["dates", "identity", "quality", "zero", "eps"])
def test_invalid_or_misaligned_endpoints_are_rejected(change):
    data = deepcopy(dashboard())
    if change == "dates":
        data["total_return"]["start_date"] = "2020-01-02"
    elif change == "identity":
        data["series"][-1]["total_return_code"] = "H00922"
    elif change == "quality":
        data["series"][0]["total_return_status"] = "error"
    elif change == "zero":
        data["series"][0]["total_return_level"] = "0"
    else:
        data["series"][0]["implied_eps"] = "999"
    with pytest.raises(ValueError):
        analyze_dashboard(data)
