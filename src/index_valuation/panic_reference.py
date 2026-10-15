"""Dated research records, kept separate from rolling bands and live market signals."""

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

REFERENCE_FILE = Path(__file__).with_name("data") / "panic-reference.json"


@lru_cache(maxsize=1)
def load_panic_reference():
    return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))


def panic_reference_for(dashboard, snapshot, specs):
    """Compare only the selected day's same-basis PE; never fill missing observations."""
    record = snapshot["indices"].get(dashboard["index"]["key"])
    selected = dashboard["selected"]
    comparison = {
        "date": selected["trade_date"],
        "pe": selected["pe"],
        "gap_pct": None,
        "reference_points": None,
        "status": "no_record",
        "method_changed": bool(record and record["method"] != dashboard["method"]),
        "pe_comparison_status": selected.get("pe_comparison_status"),
    }
    if record:
        pe = Decimal(str(selected["pe"])) if selected["pe"] is not None else None
        if dashboard["requested_date"] is not None and selected["trade_date"] < date.fromisoformat(
            snapshot["study_date"]
        ):
            comparison["status"] = "before_study"
        elif record["threshold_pe"] is None:
            comparison["status"] = record["status"]
        elif pe is None or not pe.is_finite() or pe <= 0:
            comparison["status"] = "missing_pe"
        elif selected.get("pe_usable") is False:
            comparison["status"] = "unusable_pe"
        elif (
            selected.get("pe_source") != record["source"]
            or selected.get("pe_basis") != record["basis"]
        ):
            comparison["status"] = "different_basis"
        else:
            comparison["status"] = "available"
            threshold = Decimal(record["threshold_pe"])
            comparison["gap_pct"] = (pe / threshold - 1) * 100
            # Anchor a horizontal price-chart reference to the selected day's
            # already-qualified EPS, never to each historical row's earnings.
            eps = selected.get("implied_eps")
            if eps is not None:
                eps = Decimal(str(eps))
                if eps.is_finite() and eps > 0:
                    comparison["reference_points"] = eps * threshold
    overview = []
    for key, spec in specs.items():
        item = snapshot["indices"].get(key)
        overview.append(
            {
                "key": key,
                "name": spec.name,
                "source": item["source"] if item else None,
                "cutoff": item["cutoff"] if item else None,
                "threshold_pe": item["threshold_pe"] if item else None,
                "status": item["status"] if item else "no_record",
                "months": item["three_year"]["n"] if item else None,
                "nonoverlap_windows": item["three_year"]["nonoverlap"]["n"] if item else None,
            }
        )
    return {
        "study_date": snapshot["study_date"],
        "rule": snapshot["rule"],
        "record": record,
        "comparison": comparison,
        "overview": overview,
    }
