"""Direct index valuation: provenance, same-day earnings, and past-only bands."""

import csv
from bisect import bisect_left, bisect_right, insort
from collections import Counter, deque
from datetime import datetime, time
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

from index_valuation.archive import json_text
from index_valuation.domain import DataError
from index_valuation.quality import compare_value
from index_valuation.valuation import quantile

METHOD = "futu_index_implied_v1"
QUANTILES = tuple(Decimal(p) for p in ("0.1", "0.25", "0.5", "0.75", "0.9"))
BASIS_NOTE = (
    "采用富途指数 PE，盈利期、加权及亏损处理尚未完整核实。隐含 EPS = 同日点位 / PE，"
    "单位为指数点；不标为已核实的 TTM EPS。其他来源的参考 PE 独立展示，未混入富途历史序列。"
)
PRICE_SOURCES = ("futu", "csindex", "cnindex", "baostock", "eastmoney")


def derive_points(spec, observations, valuations):
    prices, values, value_times = {}, {}, {}
    for row in observations:
        if row.symbol != spec.quote_code or row.currency != spec.currency:
            raise DataError("Index price identity or currency mismatch")
        if row.price_basis != "unadjusted_close" or not row.price.is_finite() or row.price <= 0:
            raise DataError("Index series requires finite unadjusted closing points")
        key = (row.trade_date, row.source)
        if key in prices:
            raise DataError("Duplicate index price")
        prices[key] = row
    for row in valuations:
        if row.symbol != spec.quote_code:
            raise DataError("Index valuation identity mismatch")
        key = (row.trade_date, row.source, row.metric, row.basis)
        if key in values:
            raise DataError("Duplicate index valuation")
        values[key] = row
        value_times[row.trade_date] = max(
            value_times.get(row.trade_date, row.observed_at), row.observed_at
        )
    dates = sorted({key[0] for key in prices} | {key[0] for key in values})
    now = datetime.now(ZoneInfo(spec.timezone))
    points = []
    for day in dates:
        if day > now.date() or (spec.history_start_date and day < spec.history_start_date):
            continue
        if day == now.date() and now.time() < time.fromisoformat(spec.close_time):
            continue
        available = {
            source: prices[(day, source)] for source in PRICE_SOURCES if (day, source) in prices
        }
        price_row = next(iter(available.values()), None)
        if price_row and price_row.quote_at > now:
            continue
        price = price_row.price if price_row else None
        checks = [
            compare_value(spec.quote_code, "price", price, row.price)
            for row in available.values()
            if row != price_row
        ]
        price_status = (
            "missing"
            if price is None
            else "unverified"
            if not checks
            else "error"
            if any(c.status == "error" for c in checks)
            else "warning"
            if any(c.status == "warning" for c in checks)
            else "pass"
        )
        pe_row = values.get((day, "futu", "pe", "futu_valuation_pe"))
        pe = pe_row.value if pe_row and pe_row.value.is_finite() and pe_row.value != 0 else None
        reference = values.get((day, "csindex", "pe_ttm", "csindex_published_rolling_pe"))
        pe_check = compare_value(spec.quote_code, "pe", pe, reference.value if reference else None)
        eps = price / pe if pe is not None and price_status in {"pass", "warning"} else None
        extras = {}
        for metric in ("pb", "ps"):
            row = values.get((day, "futu", metric, f"futu_valuation_{metric}"))
            extras[metric] = row.value if row else None
        points.append(
            {
                "trade_date": day,
                "price": price,
                "pe": pe,
                "implied_eps": eps,
                **extras,
                "price_source": price_row.source if price_row else None,
                "pe_source": "futu" if pe is not None else None,
                "pe_basis": "futu_valuation_pe",
                "basis_status": "unconfirmed",
                "price_status": price_status,
                "pe_comparison_status": pe_check.status,
                "reference_pe": reference.value if reference else None,
                "reference_pe_source": reference.source if reference else None,
                "reference_pe_basis": reference.basis if reference else None,
                "pe_relative_error": pe_check.relative_error,
                "phase": "backcast"
                if spec.inception_date and day < spec.inception_date
                else "published",
                "source_prices": {key: row.price for key, row in available.items()},
                "price_artifacts": {key: row.artifact_path for key, row in available.items()},
                "pe_artifact": pe_row.artifact_path if pe_row else None,
                "reference_pe_artifact": reference.artifact_path if reference else None,
                "source_observed_at": max(
                    [r.observed_at for r in available.values()]
                    + ([value_times[day]] if day in value_times else [])
                ),
            }
        )
    return points


def years_before(day, years):
    if not years:
        return day.min
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def build_dashboard(spec, points, *, as_of=None, chart_years=5, history_years=5):
    """Use only dates strictly before each point for historical PE statistics."""
    if chart_years not in {0, 1, 3, 5, 10} or history_years not in {0, 3, 5, 10}:
        raise DataError("Unsupported history window")
    if not points:
        raise DataError("尚无指数数据，请先运行 backfill-index")
    points = sorted(points, key=lambda row: row["trade_date"])
    cutoff = as_of or points[-1]["trade_date"]
    eligible = [p for p in points if p["trade_date"] <= cutoff]
    if not eligible:
        raise DataError("所选日期早于现有数据")
    selected = eligible[-1]
    first_pe_date = next(
        (p["trade_date"] for p in eligible if p["pe"] is not None and p["pe"] > 0), None
    )
    chart_start = years_before(selected["trade_date"], chart_years)
    history, queue, series = [], deque(), []
    history_total = Decimal(0)
    latest_bands = None
    for point in eligible:
        day, pe, eps = point["trade_date"], point["pe"], point["implied_eps"]
        lower = years_before(day, history_years)
        while queue and queue[0][0] < lower:
            _, old = queue.popleft()
            history.pop(bisect_left(history, old))
            history_total -= old
        enough = len(history) >= spec.band_min_observations
        mean_pe = history_total / len(history) if enough else None
        # Earlier PE is useful even on a day whose price / EPS is missing.
        positive_pe = pe is not None and pe > 0
        usable_eps = eps is not None and eps > 0 and positive_pe
        quantiles = (
            {str(q): quantile(history, q) for q in QUANTILES}
            if enough and day >= chart_start
            else {}
        )
        percentile = (
            Decimal(bisect_left(history, pe) + bisect_right(history, pe)) / 2 / len(history) * 100
            if enough and positive_pe
            else None
        )
        bands = {
            "fixed": {str(m): eps * m for m in spec.band_multiples} if usable_eps else {},
            "mean_pe": mean_pe,
            "mean_points": eps * mean_pe if usable_eps and mean_pe is not None else None,
            "quantile_pe": quantiles,
            "quantile_points": {key: eps * value for key, value in quantiles.items()}
            if usable_eps
            else {},
            "percentile": percentile,
            "history_count": len(history),
            # This flags a short available span, not missing individual sessions.
            "history_span_short": bool(
                history_years and (first_pe_date is None or first_pe_date > lower)
            ),
            "history_start": queue[0][0] if queue else None,
            "history_end": queue[-1][0] if queue else None,
        }
        if day >= chart_start:
            series.append(
                {
                    **{
                        k: point.get(k)
                        for k in (
                            "trade_date",
                            "price",
                            "pe",
                            "implied_eps",
                            "pb",
                            "ps",
                            "price_source",
                            "pe_source",
                            "pe_basis",
                            "basis_status",
                            "price_status",
                            "pe_comparison_status",
                            "reference_pe",
                            "reference_pe_source",
                            "reference_pe_basis",
                            "pe_relative_error",
                            "phase",
                        )
                    },
                    **bands,
                }
            )
        latest_bands = bands
        if positive_pe:
            insort(history, pe)
            queue.append((day, pe))
            history_total += pe
    return {
        "index": {
            "key": spec.key,
            "name": spec.name,
            "code": spec.quote_code,
            "inception_date": spec.inception_date,
            "market": spec.market,
            "currency": spec.currency,
            "band_multiples": spec.band_multiples,
        },
        "method": METHOD,
        "basis_note": BASIS_NOTE,
        "available_start": points[0]["trade_date"],
        "available_end": points[-1]["trade_date"],
        "available_sessions": len(points),
        "requested_date": as_of,
        "latest_complete_date": next(
            (p["trade_date"] for p in reversed(points) if p["implied_eps"] is not None), None
        ),
        "chart_years": chart_years,
        "history_years": history_years,
        "minimum_history": spec.band_min_observations,
        "selected": {**selected, **latest_bands},
        "series": series,
        "coverage": {
            "price": sum(p["price"] is not None for p in points),
            "pe": sum(p["pe"] is not None for p in points),
            "implied_eps": sum(p["implied_eps"] is not None for p in points),
            "price_sources": dict(Counter(p["price_source"] for p in points)),
            "pe_comparisons": dict(Counter(p["pe_comparison_status"] for p in points)),
        },
    }


def series_csv(dashboard):
    rows = dashboard["series"]
    fields = [
        "trade_date",
        "price",
        "pe",
        "implied_eps",
        "pb",
        "ps",
        "mean_pe",
        "mean_points",
        "percentile",
        "history_years",
        "history_count",
        "history_span_short",
        "history_start",
        "history_end",
        "price_source",
        "pe_source",
        "pe_basis",
        "basis_status",
        "price_status",
        "reference_pe",
        "reference_pe_source",
        "reference_pe_basis",
        "pe_comparison_status",
        "pe_relative_error",
        "phase",
    ]
    multiples = sorted(map(str, dashboard["index"]["band_multiples"]), key=Decimal)
    fields += [f"band_{m}x" for m in multiples]
    fields += [f"quantile_{q}" for q in map(str, QUANTILES)]
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **{k: row.get(k) for k in fields if not k.startswith(("band_", "quantile_"))},
                "history_years": dashboard["history_years"],
                **{f"band_{m}x": row["fixed"].get(m) for m in multiples},
                **{f"quantile_{q}": row["quantile_points"].get(str(q)) for q in QUANTILES},
            }
        )
    return "\ufeff" + output.getvalue()


class IndexSeries:
    def __init__(self, repository, output_dir):
        self.repository, self.output_dir = repository, output_dir

    def refresh(self, spec):
        self.repository.ensure_index(spec)
        with self.repository.lock(spec.key):
            observations, valuations = self.repository.index_inputs(spec)
            points = derive_points(spec, observations, valuations)
            if not points:
                raise DataError("No collected index inputs")
            run_id = self.repository.start_run(spec, points[-1]["trade_date"])
            try:
                self.repository.save_index_series(spec, METHOD, run_id, points)
                dashboard = build_dashboard(spec, points, chart_years=0)
                self.output_dir.mkdir(parents=True, exist_ok=True)
                path = self.output_dir / f"{spec.key}-index-bands-{points[-1]['trade_date']}.csv"
                path.write_text(series_csv(dashboard), encoding="utf-8")
                report = {
                    "mode": "direct_index",
                    "method": METHOD,
                    "status": "collected",
                    "index": spec.key,
                    "run_id": str(run_id),
                    "first_date": points[0]["trade_date"],
                    "last_date": points[-1]["trade_date"],
                    "rows": len(points),
                    "coverage": dashboard["coverage"],
                    "basis_status": "unconfirmed",
                    "basis_note": BASIS_NOTE,
                    "latest_complete_date": dashboard["latest_complete_date"],
                    "latest": dashboard["selected"],
                    "csv": str(path),
                }
                report_path = self.output_dir / f"{spec.key}-index-series-{run_id}.json"
                report["report_path"] = str(report_path)
                report_path.write_text(json_text(report), encoding="utf-8")
            except Exception as error:
                self.repository.finish_run(
                    run_id, "failed", {"error": str(error), "mode": "direct_index"}
                )
                raise
            self.repository.finish_run(run_id, "collected", report)
            return report
