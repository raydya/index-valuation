"""Run fixed stress screens on archived, hashed index inputs; never change production data."""

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from index_valuation.archive import json_text
from index_valuation.domain import IndexSpec
from index_valuation.panic_study import RULES, analyze
from index_valuation.quality import compare_value

NUMBERS = ("price", "pe", "implied_eps", "pb", "ps", "reference_pe", "pe_relative_error")


def snapshot(directory):
    """Export configured indices read-only; refuse to replace an existing research snapshot."""
    if (directory / "manifest.json").exists() or list(directory.glob("*-input.json")):
        raise ValueError("Snapshot already exists; reuse it or choose a new output directory")
    from index_valuation.config import Settings
    from index_valuation.db import build_engine
    from index_valuation.index_series import method_for
    from index_valuation.registry import load_indices
    from index_valuation.repository import Repository

    settings = Settings()
    engine = build_engine(settings)
    repository = Repository(engine)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"observed_at": datetime.now(UTC), "indices": {}}
    try:
        for key, spec in load_indices(settings.indices_file).items():
            points = repository.index_series(key, method_for(spec))
            if not points:
                raise ValueError(f"No stored data for {key}; update the index before research")
            _, valuations = repository.index_inputs(spec)
            refs = [
                v
                for v in valuations
                if v.metric in {"pe", "pe_ttm"} and v.source != spec.valuation_source
            ]
            content = json_text(
                {
                    "spec": spec,
                    "method": method_for(spec),
                    "points": points,
                    "reference_valuations": refs,
                }
            )
            path = directory / f"{key}-input.json"
            path.write_text(content)
            pe_rows = [p for p in points if p["pe"] is not None]
            manifest["indices"][key] = {
                "path": str(path),
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "rows": len(points),
                "first_price": points[0]["trade_date"],
                "last_price": points[-1]["trade_date"],
                "pe_rows": len(pe_rows),
                "first_pe": pe_rows[0]["trade_date"] if pe_rows else None,
                "last_pe": pe_rows[-1]["trade_date"] if pe_rows else None,
                "price_quality": dict(Counter(p["price_status"] for p in points)),
                "pe_quality": dict(Counter(p["pe_comparison_status"] for p in pe_rows)),
                "pe_phase": dict(Counter(p["phase"] for p in pe_rows)),
                "pe_reference_sources": dict(Counter(v.source for v in refs)),
            }
    finally:
        engine.dispose()
    (directory / "manifest.json").write_text(json_text(manifest))


def restore(payload):
    values = dict(payload["spec"])
    for key in ("inception_date", "history_start_date"):
        if values[key]:
            values[key] = date.fromisoformat(values[key])
    values["band_multiples"] = tuple(map(Decimal, values["band_multiples"]))
    spec = IndexSpec(**values)
    points = []
    for item in payload["points"]:
        points.append(
            {
                **item,
                "trade_date": date.fromisoformat(item["trade_date"]),
                **{
                    key: Decimal(item[key]) if item.get(key) is not None else None
                    for key in NUMBERS
                },
            }
        )
    return spec, points


def references(payload, spec, points):
    groups = defaultdict(dict)
    for row in payload["reference_valuations"]:
        group = groups[row["source"], row["metric"], row["basis"]]
        group[date.fromisoformat(row["trade_date"])] = row
    for (source, _metric, basis), values in groups.items():
        if source not in {"csindex", "danjuan"} or len(values) < 52:
            continue
        rows = []
        for point in points:
            value = values.get(point["trade_date"])
            pe = Decimal(value["value"]) if value else None
            # This is a separate supplier-definition study, not a blend of providers.
            comparison = compare_value(spec.quote_code, "pe", pe, point["pe"])
            rows.append(
                {
                    **point,
                    "pe": pe,
                    "implied_eps": None,
                    "pe_source": source if value else None,
                    "pe_basis": basis,
                    "pe_comparison_status": comparison.status,
                    "reference_pe": point["pe"],
                    "pe_relative_error": comparison.relative_error,
                    "pe_artifact": value["artifact_path"] if value else None,
                    "reference_pe_source": point.get("pe_source"),
                    "reference_pe_basis": point.get("pe_basis"),
                    "reference_pe_artifact": point.get("pe_artifact"),
                    "pe_usable": True,
                }
            )
        yield (
            source,
            replace(
                spec,
                valuation_source=source,
                band_min_observations=52 if source == "danjuan" else 252,
            ),
            rows,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument(
        "--snapshot", action="store_true", help="First export the database read-only"
    )
    args = parser.parse_args()
    directory = args.input_dir
    if args.snapshot:
        try:
            snapshot(directory)
        except ValueError as error:
            parser.error(str(error))
    manifest = json.loads((directory / "manifest.json").read_text())
    project = Path(__file__).resolve().parents[1]
    code_hashes = {
        filename: hashlib.sha256((project / filename).read_bytes()).hexdigest()
        for filename in (
            "scripts/analyze_panic_ranges.py",
            "src/index_valuation/panic_study.py",
            "src/index_valuation/index_series.py",
            "src/index_valuation/valuation.py",
        )
    }
    report = {
        "analysis_at": datetime.now(UTC),
        "code_sha256": code_hashes,
        "manifest": str(directory / "manifest.json"),
        "protocol": {
            "main_rule": "past_5y_PE_percentile<=10 AND price/prior_252_session_peak-1<=-20%",
            "rules_fixed_before_outcomes": RULES,
            "sampling": "first eligible actual PE observation each completed month",
            "entry": "next recorded trading session close; bad endpoints excluded, not skipped",
            "history": "complete 5/3/10 calendar years since first usable post-launch PE",
            "quality": "verified endpoints, >=95% verified path, no price errors or gaps >14 days",
            "pe_policy": "existing provider policy; separate exclusion-of-known-errors sensitivity",
            "returns": "price return only; Decimal, actual days/365.25; no dividends/fees/FX",
            "data_vintage": "retrospective vendor series, not original publication-vintage data",
            "era": "pre2020 requires exit before 2020; 2020plus requires entry from 2020",
        },
        "indices": {},
    }
    all_samples, all_signals = [], []
    for key, metadata in manifest["indices"].items():
        path = directory / f"{key}-input.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
        payload = json.loads(path.read_text())
        spec, points = restore(payload)
        variants = [
            ("primary_5y", spec, points, 5, False),
            ("primary_3y", spec, points, 3, False),
            ("primary_10y", spec, points, 10, False),
            ("exclude_pe_errors_5y", spec, points, 5, True),
        ]
        for source, ref_spec, ref_points in references(payload, spec, points):
            variants.append((f"reference_{source}_5y", ref_spec, ref_points, 5, False))
        result = {"name": spec.name, "coverage": metadata, "variants": {}}
        for variant, study_spec, study_points, years, strict in variants:
            summary, samples, signals = analyze(
                study_spec, study_points, history_years=years, exclude_pe_errors=strict
            )
            result["variants"][variant] = summary
            for row in samples:
                all_samples.append(
                    {"index": key, "variant": variant, **row, "rules": ";".join(row["rules"])}
                )
            for row in signals:
                all_signals.append(
                    {
                        "index": key,
                        "variant": variant,
                        **{
                            k: row[k]
                            for k in (
                                "trade_date",
                                "price",
                                "pe",
                                "mean_pe",
                                "percentile",
                                "pe_to_mean",
                                "drawdown_252",
                                "history_count",
                                "history_start",
                                "history_end",
                                "pe_comparison_status",
                            )
                        },
                    }
                )
        report["indices"][key] = result
        primary = result["variants"]["primary_5y"]
        key_stats = [
            s
            for s in primary["summaries"]
            if s["rule"] == "panic_p10_dd20" and s["era"] == "all" and s["horizon_years"] in {1, 3}
        ]
        print(
            json_text(
                {
                    "index": key,
                    "current": primary["current"],
                    "panic": [
                        {k: v for k, v in s.items() if k != "nonoverlap_samples"} for s in key_stats
                    ],
                }
            ),
            flush=True,
        )
    (directory / "summary.json").write_text(json_text(report))
    for name, rows in (("outcomes.csv", all_samples), ("signals.csv", all_signals)):
        with (directory / name).open("w", encoding="utf-8-sig", newline="") as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    print(f"Saved research results to {directory}", flush=True)


if __name__ == "__main__":
    main()
