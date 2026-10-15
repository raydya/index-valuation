"""Export a compact, reviewable web record from an existing research snapshot.

Does not run research, query the database or overwrite an existing output. Review
the exported notes before publishing it as src/index_valuation/data/panic-reference.json.
"""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from index_valuation.archive import json_text


def outcome(primary, years, rule="panic_p10_dd20"):
    row = next(
        row
        for row in primary["summaries"]
        if row["rule"] == rule and row["horizon_years"] == years and row["era"] == "all"
    )
    result = {
        name: row.get(name)
        for name in (
            "n",
            "median_annualized_return",
            "worst_loss_from_entry",
            "observed_pe_range",
        )
    }
    if years == 3 and rule == "panic_p10_dd20":
        result["nonoverlap"] = {"n": row["nonoverlap"]["n"]}
        result["nonoverlap_samples"] = [
            {
                name: sample[name]
                for name in (
                    "signal_date",
                    "signal_pe",
                    "percentile",
                    "signal_drawdown",
                    "entry_date",
                    "exit_date",
                    "annualized_return",
                    "lowest_return_from_entry",
                    "pe_comparison_status",
                )
            }
            for sample in row["nonoverlap_samples"]
        ]
    return result


def export_reference(directory):
    summary_bytes = (directory / "summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    records = {}
    for key, item in summary["indices"].items():
        metadata = manifest["indices"][key]
        raw = (directory / Path(metadata["path"]).name).read_bytes()
        if (
            hashlib.sha256(raw).hexdigest() != metadata["sha256"]
            or metadata["sha256"] != item["coverage"]["sha256"]
        ):
            raise ValueError(f"Research input hash mismatch: {key}")
        inputs = json.loads(raw)
        primary = item["variants"]["primary_5y"]
        current = primary["current"]

        status = (
            "missing_pe"
            if not metadata["pe_rows"]
            else "insufficient_history"
            if not current["history_complete"]
            else "reference"
        )
        basis = next((p["pe_basis"] for p in reversed(inputs["points"]) if p["pe"]), None)
        records[key] = {
            "name": item["name"],
            "source": primary["source"],
            "basis": basis,
            "method": inputs["method"],
            "cutoff": primary["cutoff"],
            "input_sha256": metadata["sha256"],
            "status": status,
            "threshold_pe": current["p10_pe"] if status == "reference" else None,
            "threshold_to_mean": current["p10_to_mean"] if status == "reference" else None,
            "history_start": current["history_start"],
            "history_end": current["history_end"],
            "history_count": current["history_count"],
            "history_complete": current["history_complete"],
            "pe_rows": metadata["pe_rows"],
            "pe_quality": metadata["pe_quality"],
            "one_year": outcome(primary, 1),
            "three_year": outcome(primary, 3),
            "baseline_three_year": {
                name: outcome(primary, 3, "all").get(name)
                for name in ("n", "median_annualized_return")
            },
            "reference_thresholds": [
                {
                    "source": variant["source"],
                    "cutoff": variant["cutoff"],
                    "threshold_pe": variant["current"]["p10_pe"],
                }
                for name, variant in item["variants"].items()
                if name.startswith("reference_")
                and name.endswith("_5y")
                and variant["current"]["history_complete"]
                and variant["current"]["p10_pe"] is not None
            ],
            "notes": [],
        }
    return {
        "schema_version": 1,
        "study_date": datetime.fromisoformat(summary["analysis_at"])
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .date()
        .isoformat(),
        "analysis_at": summary["analysis_at"],
        "input_observed_at": manifest["observed_at"],
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "research_code_sha256": summary["code_sha256"],
        "rule": "当时此前5年 PE 分位 ≤10%，且较近252个价格交易日收盘高点下跌 ≥20%。",
        "protocol": summary["protocol"],
        "indices": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    record = export_reference(args.directory)
    with args.output.open("x", encoding="utf-8") as target:
        target.write(json_text(record) + "\n")
    print(
        f"Exported {len(record['indices'])} indices to {args.output}; "
        "review notes before publishing"
    )


if __name__ == "__main__":
    main()
