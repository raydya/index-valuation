"""Past-only valuation stress screens and subsequent index price outcomes.

This is conditional historical research, not a fitted or executable strategy.
Provider history is not a vintage point-in-time earnings database.
"""

from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from statistics import median

from index_valuation.index_series import build_dashboard

RULES = {
    "all": (None, None, None),
    "pe_p05": (Decimal(5), None, None),
    "pe_p10": (Decimal(10), None, None),
    "pe_p20": (Decimal(20), None, None),
    "drawdown20": (None, Decimal("-0.20"), None),
    "panic_p05_dd20": (Decimal(5), Decimal("-0.20"), None),
    "panic_p10_dd20": (Decimal(10), Decimal("-0.20"), None),
    "panic_p20_dd20": (Decimal(20), Decimal("-0.20"), None),
    "mean80": (None, None, Decimal("0.8")),
    "mean80_dd20": (None, Decimal("-0.20"), Decimal("0.8")),
}
GOOD_PRICE = {"pass", "warning"}
HORIZONS = (1, 3, 5)


def anniversary(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def positive(value):
    return value is not None and value.is_finite() and value > 0


def matches(row, rule):
    percentile, drawdown, ratio = RULES[rule]
    return (
        (percentile is None or row["percentile"] <= percentile)
        and (drawdown is None or row["drawdown_252"] <= drawdown)
        and (ratio is None or row["pe_to_mean"] <= ratio)
    )


def prepare(points, *, exclude_pe_errors=False):
    """Keep the application's method policy, with a separate stricter sensitivity."""
    result = []
    for point in points:
        row = dict(point)
        if row["phase"] != "published" or (
            exclude_pe_errors and row["pe_comparison_status"] == "error"
        ):
            row["pe_usable"] = False
            row["implied_eps"] = None
        result.append(row)
    return result


def price_window_ok(rows):
    if not rows or any(not positive(r["price"]) or r["price_status"] == "error" for r in rows):
        return False
    if any(
        (b["trade_date"] - a["trade_date"]).days > 14 for a, b in zip(rows, rows[1:], strict=False)
    ):
        return False
    return sum(r["price_status"] in GOOD_PRICE for r in rows) / len(rows) >= 0.95


def features(spec, points, *, history_years=5, peak_sessions=252):
    """Reuse application statistics, including completed-week sampling for US PE."""
    if len({p["trade_date"] for p in points}) != len(points):
        raise ValueError("Duplicate research dates")
    points = sorted(points, key=lambda p: p["trade_date"])
    dashboard = build_dashboard(spec, points, chart_years=0, history_years=history_years)
    first_pe = next(
        (p["trade_date"] for p in points if positive(p["pe"]) and p.get("pe_usable", True)),
        None,
    )
    price_rows = [p for p in points if positive(p["price"])]
    dates = [p["trade_date"] for p in price_rows]
    result = []
    for row in dashboard["series"]:
        day = row["trade_date"]
        stop = bisect_right(dates, day)
        window = price_rows[max(0, stop - peak_sessions) : stop]
        peak_ok = len(window) == peak_sessions and price_window_ok(window)
        peak = max((r["price"] for r in window), default=None) if peak_ok else None
        price_verified = row["price_status"] in GOOD_PRICE
        history_complete = (
            first_pe is not None
            and day >= anniversary(first_pe, history_years)
            and row["mean_pe"] is not None
            and row["history_count"] >= spec.band_min_observations
        )
        pe_ok = positive(row["pe"]) and row.get("pe_usable", True)
        result.append(
            {
                **row,
                "history_complete": history_complete,
                "drawdown_252": row["price"] / peak - 1
                if peak and positive(row["price"])
                else None,
                "peak_252": peak,
                "pe_to_mean": row["pe"] / row["mean_pe"] if pe_ok and row["mean_pe"] else None,
                "eligible": bool(
                    history_complete
                    and pe_ok
                    and price_verified
                    and peak_ok
                    and row["phase"] == "published"
                    and row["percentile"] is not None
                ),
            }
        )
    return result


def monthly_signals(rows, cutoff):
    """First eligible actual PE observation each completed month; no PE filling."""
    selected = {}
    for row in rows:
        day = row["trade_date"]
        month = (day.year, day.month)
        if row["eligible"] and month < (cutoff.year, cutoff.month):
            selected.setdefault(month, row)
    return list(selected.values())


def outcome(signal, price_rows, years):
    """Enter at the next recorded session close; reject bad endpoints, never skip them."""
    dates = [p["trade_date"] for p in price_rows]
    entry_idx = bisect_right(dates, signal["trade_date"])
    if entry_idx == len(dates) or (dates[entry_idx] - signal["trade_date"]).days > 14:
        return None, "missing_next_session"
    entry = price_rows[entry_idx]
    if not positive(entry["price"]) or entry["price_status"] not in GOOD_PRICE:
        return None, "unverified_entry"
    target = anniversary(entry["trade_date"], years)
    if target > dates[-1]:
        return None, "immature"
    exit_idx = bisect_left(dates, target)
    if (dates[exit_idx] - target).days > 14:
        return None, "missing_exit"
    exit_row = price_rows[exit_idx]
    if not positive(exit_row["price"]) or exit_row["price_status"] not in GOOD_PRICE:
        return None, "unverified_exit"
    path = price_rows[entry_idx : exit_idx + 1]
    if not price_window_ok(path):
        return None, "unverified_or_gapped_path"
    with localcontext() as ctx:
        ctx.prec = 34
        growth = exit_row["price"] / entry["price"]
        days = (exit_row["trade_date"] - entry["trade_date"]).days
        high, drawdown = entry["price"], Decimal(0)
        for row in path:
            high = max(high, row["price"])
            drawdown = min(drawdown, row["price"] / high - 1)
        values = {
            "cumulative_return": growth - 1,
            "annualized_return": growth ** (Decimal("365.25") / days) - 1,
            "lowest_return_from_entry": min(r["price"] for r in path) / entry["price"] - 1,
            "maximum_close_drawdown": drawdown,
        }
    return {
        "signal_date": signal["trade_date"],
        "signal_pe": signal["pe"],
        "percentile": signal["percentile"],
        "pe_to_mean": signal["pe_to_mean"],
        "signal_drawdown": signal["drawdown_252"],
        "pe_comparison_status": signal["pe_comparison_status"],
        "entry_date": entry["trade_date"],
        "entry_price": entry["price"],
        "horizon_years": years,
        "exit_date": exit_row["trade_date"],
        "exit_price": exit_row["price"],
        "path_verified_fraction": Decimal(sum(r["price_status"] in GOOD_PRICE for r in path))
        / len(path),
        **values,
    }, None


def nonoverlapping(samples):
    result = []
    for row in sorted(samples, key=lambda s: s["entry_date"]):
        if not result or row["entry_date"] >= result[-1]["exit_date"]:
            result.append(row)
    return result


def summarize(samples):
    if not samples:
        return {"n": 0}
    returns = [s["annualized_return"] for s in samples]
    return {
        "n": len(samples),
        "first_signal": min(s["signal_date"] for s in samples),
        "last_signal": max(s["signal_date"] for s in samples),
        "signal_years": dict(Counter(s["signal_date"].year for s in samples)),
        "median_annualized_return": median(returns),
        "positive_fraction": Decimal(sum(r > 0 for r in returns)) / len(returns),
        "worst_annualized_return": min(returns),
        "worst_loss_from_entry": min(s["lowest_return_from_entry"] for s in samples),
        "median_loss_from_entry": median(s["lowest_return_from_entry"] for s in samples),
        "worst_close_drawdown": min(s["maximum_close_drawdown"] for s in samples),
        "observed_pe_range": [
            min(s["signal_pe"] for s in samples),
            max(s["signal_pe"] for s in samples),
        ],
        "pe_quality": dict(Counter(s["pe_comparison_status"] for s in samples)),
    }


def analyze(spec, points, *, history_years=5, exclude_pe_errors=False):
    prepared = prepare(points, exclude_pe_errors=exclude_pe_errors)
    feature_rows = features(spec, prepared, history_years=history_years)
    cutoff = max(p["trade_date"] for p in prepared)
    signals = monthly_signals(feature_rows, cutoff)
    # A known session with missing price is still an endpoint candidate. Removing
    # it here would silently move entry/exit to a later, potentially better price.
    price_rows = sorted(prepared, key=lambda p: p["trade_date"])
    samples, exclusions = [], Counter()
    for signal in signals:
        for years in HORIZONS:
            row, reason = outcome(signal, price_rows, years)
            if reason:
                exclusions[f"{years}y:{reason}"] += 1
            else:
                row["rules"] = [rule for rule in RULES if matches(signal, rule)]
                samples.append(row)
    summaries = []
    for years in HORIZONS:
        for era in ("all", "pre2020", "2020plus"):
            eligible = [
                s
                for s in samples
                if s["horizon_years"] == years
                and (era != "pre2020" or s["exit_date"] < date(2020, 1, 1))
                and (era != "2020plus" or s["entry_date"] >= date(2020, 1, 1))
            ]
            for rule in RULES:
                members = [s for s in eligible if rule in s["rules"]]
                nonoverlap = nonoverlapping(members)
                summaries.append(
                    {
                        "rule": rule,
                        "horizon_years": years,
                        "era": era,
                        **summarize(members),
                        "nonoverlap": summarize(nonoverlap),
                        "nonoverlap_samples": nonoverlap,
                    }
                )
    last_pe = next((r for r in reversed(feature_rows) if positive(r["pe"])), None)
    recent = feature_rows[-1]
    threshold = recent["quantile_pe"].get("0.1")
    current = {
        "date": recent["trade_date"],
        "pe_date": last_pe["trade_date"] if last_pe else None,
        "pe": recent["pe"],
        "mean_pe": recent["mean_pe"],
        "p10_pe": threshold,
        "p10_to_mean": threshold / recent["mean_pe"] if threshold and recent["mean_pe"] else None,
        "percentile": recent["percentile"],
        "pe_to_mean": recent["pe_to_mean"],
        "drawdown_252": recent["drawdown_252"],
        "history_count": recent["history_count"],
        "history_start": recent["history_start"],
        "history_end": recent["history_end"],
        "history_complete": recent["history_complete"],
        "eligible": recent["eligible"],
        "panic": matches(recent, "panic_p10_dd20") if recent["eligible"] else None,
        "price_status": recent["price_status"],
        "pe_comparison_status": recent["pe_comparison_status"],
        "pe_usable": recent["pe_usable"],
    }
    return (
        {
            "history_years": history_years,
            "exclude_pe_errors": exclude_pe_errors,
            "source": spec.valuation_source,
            "cutoff": cutoff,
            "current": current,
            "monthly_signals": len(signals),
            "signal_exclusions": dict(exclusions),
            "summaries": summaries,
        },
        samples,
        signals,
    )
