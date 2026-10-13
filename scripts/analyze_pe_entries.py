"""Compare historical PE signals with subsequent price returns from a dashboard CSV.

Research only: month-end observations overlap, returns exclude dividends and fees,
and source PE definitions are kept separate. No order execution or threshold fitting.
"""

import argparse
import csv
import hashlib
import json
from bisect import bisect_left
from collections import Counter, deque
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import median

from index_valuation.index_series import years_before

PE_LIMITS = tuple(map(Decimal, ("8", "10", "12", "15", "20")))
PE_LABELS = ("<8", "8–10", "10–12", "12–15", "15–20", ">=20")
RATIO_LIMITS = tuple(map(Decimal, ("0.8", "0.9", "1.0", "1.1")))
RATIO_LABELS = ("<0.8", "0.8–0.9", "0.9–1.0", "1.0–1.1", ">=1.1")


def number(value):
    if not value:
        return None
    value = Decimal(value)
    return value if value.is_finite() else None


def bucket(value, limits, labels):
    return next(
        (label for bound, label in zip(limits, labels, strict=False) if value < bound), labels[-1]
    )


def anniversary(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def histories(rows, field):
    window, total, first, result = deque(), Decimal(0), None, {}
    for row in rows:
        day, pe = row["day"], row[field]
        lower = years_before(day, 5)
        while window and window[0][0] < lower:
            total -= window.popleft()[1]
        enough = first is not None and day >= anniversary(first, 5) and len(window) >= 252
        result[day] = total / len(window) if enough else None
        if pe is not None and pe > 0:
            if first is None:
                first = day
            window.append((day, pe))
            total += pe
    return result


def summarize(samples):
    if not samples:
        return {"n": 0}
    returns = [s["annualized_price_return"] for s in samples]
    return {
        "n": len(samples),
        "first_signal": samples[0]["signal_date"],
        "last_signal": samples[-1]["signal_date"],
        "signals_by_year": dict(Counter(s["signal_date"].year for s in samples)),
        "median_annualized_price_return": median(returns),
        "mean_annualized_price_return": sum(returns) / len(returns),
        "positive_return_fraction": Decimal(sum(r > 0 for r in returns)) / len(returns),
        "worst_annualized_price_return": min(returns),
        "median_loss_from_entry": median(s["lowest_return_from_entry"] for s in samples),
        "worst_loss_from_entry": min(s["lowest_return_from_entry"] for s in samples),
    }


def analyze(input_path, output_dir):
    raw = list(csv.DictReader(input_path.open(encoding="utf-8-sig")))
    rows = [
        {
            **row,
            "day": date.fromisoformat(row["trade_date"]),
            **{key: number(row[key]) for key in ("price", "pe", "reference_pe", "implied_eps")},
        }
        for row in raw
    ]
    rows.sort(key=lambda row: row["day"])
    assert len({r["day"] for r in rows}) == len(rows), "Duplicate dates"
    last_day = rows[-1]["day"]
    monthly = {}
    for row in rows:
        # Exclude the cutoff month, whose final trading session may not yet be known.
        if (row["day"].year, row["day"].month) < (last_day.year, last_day.month):
            monthly[row["day"].year, row["day"].month] = row
    prices = [
        r
        for r in rows
        if r["price"] and r["price"] > 0 and r["price_status"] in {"pass", "warning"}
    ]
    dates = [r["day"] for r in prices]
    price_by_day = {r["day"]: r for r in prices}
    signals, summaries = [], []
    for source, field in (("futu", "pe"), ("csindex", "reference_pe")):
        means = histories(rows, field)
        for row in monthly.values():
            day, pe = row["day"], row[field]
            if row["phase"] != "published" or day not in price_by_day or pe is None or pe <= 0:
                continue
            mean_pe = means[day]
            ratio = pe / mean_pe if mean_pe else None
            entry_index = bisect_left(dates, day)
            for years in (1, 3, 5):
                target = anniversary(day, years)
                exit_index = bisect_left(dates, target)
                if exit_index == len(prices) or (dates[exit_index] - target).days > 10:
                    continue
                exit_row = prices[exit_index]
                growth = exit_row["price"] / row["price"]
                elapsed_years = Decimal((exit_row["day"] - day).days) / Decimal("365.2425")
                lowest = min(r["price"] for r in prices[entry_index : exit_index + 1])
                signals.append(
                    {
                        "source": source,
                        "signal_date": day,
                        "signal_price": row["price"],
                        "pe": pe,
                        "mean_pe_5y": mean_pe,
                        "pe_to_mean": ratio,
                        "pe_band": bucket(pe, PE_LIMITS, PE_LABELS),
                        "ratio_band": bucket(ratio, RATIO_LIMITS, RATIO_LABELS) if ratio else None,
                        "pe_comparison_status": row["pe_comparison_status"],
                        "horizon_years": years,
                        "exit_date": exit_row["day"],
                        "exit_price": exit_row["price"],
                        "cumulative_price_return": growth - 1,
                        "annualized_price_return": growth ** (1 / elapsed_years) - 1,
                        "lowest_return_from_entry": lowest / row["price"] - 1,
                    }
                )
        for era, since in (("all", date.min), ("2016+", date(2016, 1, 1))):
            for field, labels in (("pe_band", PE_LABELS), ("ratio_band", RATIO_LABELS)):
                for years in (1, 3, 5):
                    eligible = [
                        s
                        for s in signals
                        if s["source"] == source
                        and s["signal_date"] >= since
                        and s["horizon_years"] == years
                        and s[field] is not None
                    ]
                    for label in (*labels, "all"):
                        members = [s for s in eligible if label == "all" or s[field] == label]
                        summaries.append(
                            {
                                "source": source,
                                "era": era,
                                "group": field,
                                "band": label,
                                "horizon_years": years,
                                **summarize(members),
                            }
                        )
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / input_path.stem
    with prefix.with_suffix(".signals.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(signals[0]))
        writer.writeheader()
        writer.writerows(signals)
    metadata = {
        "input": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "data_cutoff": last_day,
        "price_return_only": True,
        "sampling": "Month-end close, excluding cutoff month and pre-launch entries",
        "horizon": "First verified close on/after calendar anniversary, at most 10 days later",
        "mean": "Prior-only positive daily PE, five calendar years available, >=252 observations",
        "buckets": "Lower inclusive, upper exclusive; fixed before inspecting returns",
        "dependence": "Monthly holding periods overlap; observations are not independent trials",
        "quality": dict(Counter(r["pe_comparison_status"] for r in rows if r["reference_pe"])),
        "summaries": summaries,
    }
    prefix.with_suffix(".summary.json").write_text(
        json.dumps(metadata, ensure_ascii=False, default=str, indent=2), encoding="utf-8"
    )
    print(prefix.with_suffix(".summary.json"))
    print(prefix.with_suffix(".signals.csv"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/research"))
    args = parser.parse_args()
    analyze(args.input_csv, args.output_dir)
