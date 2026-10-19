"""Decompose saved dashboard returns; never collect or mutate valuation data.

This is an endpoint identity, not causal attribution to constituent-company
profits. The three geometric factors multiply; their ordinary CAGRs do not add.
"""

import argparse
import hashlib
import json
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from index_valuation.archive import json_text


def positive(value):
    if value is None:
        raise ValueError("Missing endpoint value")
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("Endpoint values must be finite and positive")
    return result


def factor_summary(factor, years):
    return {
        "factor": factor,
        "period_change_pct": (factor - 1) * 100,
        "annualized_change_pct": (factor ** (1 / years) - 1) * 100,
        "annualized_log_contribution": factor.ln() / years * 100,
    }


def pe_split(first, last, price_factor, years, *, reference=False):
    field, source_field, basis_field = (
        ("reference_pe", "reference_pe_source", "reference_pe_basis")
        if reference
        else ("pe", "pe_source", "pe_basis")
    )
    if first.get(field) is None or last.get(field) is None:
        return {"status": "missing_endpoint_pe"}
    source, basis = first.get(source_field), first.get(basis_field)
    if (
        not source
        or not basis
        or source != last.get(source_field)
        or basis != last.get(basis_field)
    ):
        return {"status": "inconsistent_pe_source_or_basis"}
    if not reference and any(p.get("pe_usable") is False for p in (first, last)):
        return {"status": "unusable_pe"}
    try:
        pe_start, pe_end = positive(first[field]), positive(last[field])
    except ValueError:
        return {"status": "invalid_endpoint_pe"}
    eps_start = positive(first["price"]) / pe_start
    eps_end = positive(last["price"]) / pe_end
    if not reference:
        for point, recomputed in ((first, eps_start), (last, eps_end)):
            stored = point.get("implied_eps")
            if stored is None:
                return {"status": "missing_usable_implied_eps"}
            if abs(positive(stored) / recomputed - 1) > Decimal("1e-12"):
                raise ValueError("Stored implicit EPS does not match its price/PE")
    valuation = pe_end / pe_start
    # Derive within the same source/basis, never use a reference value to fill
    # a missing primary value or combine sources across the two endpoints.
    earnings = price_factor / valuation
    return {
        "status": "available",
        "source": source,
        "basis": basis,
        "pe_start": pe_start,
        "pe_end": pe_end,
        "eps_start": eps_start,
        "eps_end": eps_end,
        "implicit_earnings": factor_summary(earnings, years),
        "valuation": factor_summary(valuation, years),
    }


def analyze_dashboard(dashboard):
    series = dashboard["series"]
    if len(series) < 2:
        raise ValueError("At least two dated observations are required")
    dates = [date.fromisoformat(p["trade_date"]) for p in series]
    if dates != sorted(set(dates)):
        raise ValueError("Dashboard dates must be unique and ordered")
    first, last = series[0], series[-1]
    if last["trade_date"] != dashboard["selected"]["trade_date"]:
        raise ValueError("Last endpoint must equal the selected valuation date")
    tr, pr = dashboard["total_return"], dashboard["price_return"]
    if tr["basis"] != "gross_total_return":
        raise ValueError("Only separately identified gross total returns are supported")
    if any(
        summary["start_date"] != first["trade_date"]
        or summary["end_date"] != last["trade_date"]
        or summary["status"] != "ok"
        for summary in (tr, pr)
    ):
        raise ValueError("Price and total-return endpoints must match exactly")
    for point in (first, last):
        if point.get("price_status") == "error" or point.get("total_return_status") == "error":
            raise ValueError("Material endpoint price/return discrepancy")
        if (
            point.get("total_return_code") != tr["index_code"]
            or point.get("total_return_basis") != tr["basis"]
            or point.get("total_return_source") != tr["source"]
        ):
            raise ValueError("Total-return identity changed at an endpoint")
    with localcontext() as context:
        context.prec = 40
        days = (dates[-1] - dates[0]).days
        years = Decimal(days) / Decimal("365.25")
        price_factor = positive(last["price"]) / positive(first["price"])
        total_factor = positive(last["total_return_level"]) / positive(first["total_return_level"])
        dividend_factor = total_factor / price_factor
        price = factor_summary(price_factor, years)
        total = factor_summary(total_factor, years)
        dividends = factor_summary(dividend_factor, years)
        for calculated, saved in ((price, pr), (total, tr)):
            if abs(
                calculated["annualized_change_pct"] - Decimal(saved["annualized_return_pct"])
            ) > Decimal("1e-12"):
                raise ValueError("Recalculated CAGR differs from dashboard")
        primary = pe_split(first, last, price_factor, years)
        reference = pe_split(first, last, price_factor, years, reference=True)
        for split in (primary, reference):
            if split["status"] == "available":
                rebuilt = (
                    split["implicit_earnings"]["factor"]
                    * split["valuation"]["factor"]
                    * dividend_factor
                )
                residual = rebuilt - total_factor
                if abs(residual) > Decimal("1e-30"):
                    raise ValueError("Factor decomposition does not reconcile")
                split["reconciliation_residual"] = residual
        fields = (
            "trade_date",
            "price",
            "pe",
            "implied_eps",
            "price_source",
            "price_status",
            "pe_source",
            "pe_basis",
            "pe_usable",
            "pe_comparison_status",
            "pe_relative_error",
            "reference_pe",
            "reference_pe_source",
            "reference_pe_basis",
            "phase",
            "total_return_level",
            "total_return_source",
            "total_return_code",
            "total_return_status",
        )
        return {
            "index": dashboard["index"]["key"],
            "name": dashboard["index"]["name"],
            "start": first["trade_date"],
            "end": last["trade_date"],
            "elapsed_days": days,
            "elapsed_years": years,
            "first_available_primary_pe": next(
                (p["trade_date"] for p in series if p.get("pe") is not None), None
            ),
            "includes_backcast": any(p.get("phase") == "backcast" for p in series),
            "endpoints": [{k: p.get(k) for k in fields} for p in (first, last)],
            "price": price,
            "total_return": total,
            "reinvestment_increment": dividends,
            "cagr_uplift_percentage_points": total["annualized_change_pct"]
            - price["annualized_change_pct"],
            "primary_decomposition": primary,
            "reference_sensitivity": reference,
            "interpretation": (
                "Source-basis endpoint identity; not constituent profit growth, "
                "dividend yield, or causal attribution."
            ),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Saved dashboard JSON snapshots")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = []
    for path in args.inputs:
        content = path.read_bytes()
        result = analyze_dashboard(json.loads(content))
        result["input"] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
        reports.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json_text(reports), encoding="utf-8")
    for result in reports:
        split = result["primary_decomposition"]
        earnings = split.get("implicit_earnings", {}).get("annualized_change_pct")
        valuation = split.get("valuation", {}).get("annualized_change_pct")
        dividend = result["reinvestment_increment"]["annualized_change_pct"]
        total = result["total_return"]["annualized_change_pct"]
        formatted = [
            "—" if x is None else f"{x:+.2f}%" for x in (earnings, valuation, dividend, total)
        ]
        print(result["name"], *formatted, split["status"])


if __name__ == "__main__":
    main()
