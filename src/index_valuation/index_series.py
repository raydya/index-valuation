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
from index_valuation.performance import price_return_summary
from index_valuation.quality import compare_value
from index_valuation.valuation import quantile

METHOD = "futu_index_implied_v1"
QUANTILES = tuple(Decimal(p) for p in ("0.1", "0.25", "0.5", "0.75", "0.9"))
BASIS_NOTE = (
    "采用富途指数 PE，盈利期、加权及亏损处理尚未完整核实。隐含 EPS = 同日点位 / PE，"
    "单位为指数点；不标为已核实的 TTM EPS。其他来源的参考 PE 独立展示，未混入富途历史序列。"
)
PRICE_SOURCES = ("futu", "csindex", "cnindex", "fred", "nasdaq", "yahoo", "baostock", "eastmoney")


def valuation_profile(spec):
    if spec.valuation_source == "csindex":
        return {
            "source": "csindex",
            "metric": "pe_ttm",
            "basis": "csindex_published_rolling_pe",
            "label": "中证官方滚动 PE",
            "history_frequency": "daily",
            "sampling_note": "按中证官方有效日度 PE 计算；其他来源的周采样仅用于同日对照。",
        }
    if spec.valuation_source == "danjuan":
        return {
            "source": "danjuan",
            "metric": "pe_ttm",
            "basis": "danjuan_published_pe_ttm",
            "label": "雪球基金 PE TTM",
            "history_frequency": "weekly",
            "sampling_note": (
                "历史 PE 为周采样，最新快照每日采集；均值和分位每个已结束自然周"
                "只取最后一个有效观测，周内不重复计数。"
            ),
        }
    return {
        "source": spec.valuation_source,
        "metric": "pe",
        "basis": "futu_valuation_pe",
        "label": "富途口径" if spec.valuation_source == "futu" else "待接入指数 PE",
        "history_frequency": "daily",
        "sampling_note": "按实际有效日度 PE 计算，不填补缺失值。",
    }


def method_for(spec):
    return {
        "futu": METHOD,
        "csindex": "csindex_index_implied_daily_v1",
        "danjuan": "danjuan_index_implied_weekly_v1",
        "none": "index_price_only_v1",
    }[spec.valuation_source]


def basis_note(spec):
    if spec.valuation_source == "csindex":
        return (
            "采用中证指数官方发布的滚动 PE；官方单张注明指数指标按成分的计算用股本计算。"
            "隐含 EPS = 同日价格指数点位 / 官方 PE，单位为指数点。"
            "雪球基金 PE 单独对照，盈利、权重及亏损处理的差异尚未完整核实。"
            "同日 PE 差异超过5%时暂停该日 EPS/band，并排除该 PE 的统计贡献；"
            "无独立 PE 对照的日期仅作官方口径参考，不混入参考源历史，不填补缺失值。"
        )
    if spec.valuation_source == "danjuan":
        return (
            "采用雪球基金（蛋卷）发布的指数 PE TTM，官方说明主要取自 Choice。"
            "加权和亏损处理尚未完整独立核实；隐含 EPS = 同日指数点位 / 该来源 PE，单位为指数点。"
            "周频历史与每日快照保留原始日期，不向前填充；均值、分位按已结束自然周的最后有效样本计算。"
            "其他来源 PE 单独对照，口径差异不代表同一盈利定义；未混入主源历史。"
        )
    if not spec.futu_supported:
        return (
            "Futu OpenD 尚不支持该指数的日线及估值。当前展示指数价格；"
            "缺少可靠的指数 PE 时，PE、隐含 EPS 和估值带保持为空。"
            "ETF 价格、基金持仓 PE 和指数点位采用不同口径，不作替代。"
        )
    return BASIS_NOTE


def pe_reference_keys(spec):
    """Keep comparison inputs separate from the primary PE's accounting basis."""
    return {
        "futu": (("csindex", "pe_ttm", "csindex_published_rolling_pe"),),
        "csindex": (("danjuan", "pe_ttm", "danjuan_published_pe_ttm"),),
        "danjuan": (
            ("multpl", "pe_ttm", "multpl_reported_ttm_current_estimate"),
            ("gurufocus", "pe", "gurufocus_published_pe_unconfirmed"),
        ),
        "none": (),
    }[spec.valuation_source]


def derive_points(spec, observations, valuations):
    profile = valuation_profile(spec)
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
        pe_row = values.get((day, profile["source"], profile["metric"], profile["basis"]))
        pe = pe_row.value if pe_row and pe_row.value.is_finite() and pe_row.value != 0 else None
        reference_checks = [
            (
                values[(day, *key)],
                compare_value(spec.quote_code, "pe", pe, values[(day, *key)].value),
            )
            for key in pe_reference_keys(spec)
            if (day, *key) in values
        ]
        reference, pe_check = max(
            reference_checks,
            key=lambda pair: pair[1].relative_error or Decimal(0),
            default=(None, compare_value(spec.quote_code, "pe", pe, None)),
        )
        eps = price / pe if pe is not None and price_status in {"pass", "warning"} else None
        # Alternative PE is allowed, but a known material discrepancy must not
        # silently become a valuation band. Keep the raw PE visible for review.
        pe_usable = not (
            spec.valuation_source in {"danjuan", "csindex"} and pe_check.status == "error"
        )
        if not pe_usable:
            eps = None
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
                "pe_source": profile["source"] if pe is not None else None,
                "pe_basis": profile["basis"] if pe_row else None,
                "basis_status": "unconfirmed",
                "price_status": price_status,
                "pe_comparison_status": pe_check.status,
                "pe_usable": pe_usable,
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
                "pe_references": [
                    {
                        "source": row.source,
                        "basis": row.basis,
                        "pe": row.value,
                        "status": check.status,
                        "relative_error": check.relative_error,
                        "artifact": row.artifact_path,
                    }
                    for row, check in reference_checks
                ],
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
    weekly = valuation_profile(spec)["history_frequency"] == "weekly"
    pending_week = None
    history_total = Decimal(0)
    latest_bands = None
    for point in eligible:
        day, pe, eps = point["trade_date"], point["pe"], point["implied_eps"]
        if weekly and pending_week and pending_week[0].isocalendar()[:2] != day.isocalendar()[:2]:
            old_day, old_pe = pending_week
            insort(history, old_pe)
            queue.append((old_day, old_pe))
            history_total += old_pe
            pending_week = None
        lower = years_before(day, history_years)
        while queue and queue[0][0] < lower:
            _, old = queue.popleft()
            history.pop(bisect_left(history, old))
            history_total -= old
        enough = len(history) >= spec.band_min_observations
        mean_pe = history_total / len(history) if enough else None
        # Earlier PE is useful even on a day whose price / EPS is missing.
        positive_pe = pe is not None and pe > 0 and point.get("pe_usable", True)
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
                            "pe_usable",
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
            if weekly:
                pending_week = (day, pe)
            else:
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
            "tracking_etfs": spec.tracking_etfs,
            "description": spec.description,
            "futu_supported": spec.futu_supported,
            "valuation": valuation_profile(spec),
        },
        "method": method_for(spec),
        "basis_note": basis_note(spec),
        "available_start": points[0]["trade_date"],
        "available_end": points[-1]["trade_date"],
        "available_sessions": len(points),
        "requested_date": as_of,
        "latest_complete_date": next(
            (p["trade_date"] for p in reversed(points) if p["implied_eps"] is not None), None
        ),
        "chart_years": chart_years,
        "price_return": price_return_summary(eligible, chart_start, selected["trade_date"]),
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
        "history_frequency",
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
        "pe_usable",
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
                "history_frequency": dashboard["index"]["valuation"]["history_frequency"],
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
                self.repository.save_index_series(spec, method_for(spec), run_id, points)
                dashboard = build_dashboard(spec, points, chart_years=0)
                self.output_dir.mkdir(parents=True, exist_ok=True)
                path = self.output_dir / f"{spec.key}-index-bands-{points[-1]['trade_date']}.csv"
                path.write_text(series_csv(dashboard), encoding="utf-8")
                report = {
                    "mode": "direct_index",
                    "method": method_for(spec),
                    "status": "collected"
                    if dashboard["selected"]["implied_eps"] is not None
                    else "partial",
                    "index": spec.key,
                    "run_id": str(run_id),
                    "first_date": points[0]["trade_date"],
                    "last_date": points[-1]["trade_date"],
                    "rows": len(points),
                    "coverage": dashboard["coverage"],
                    "basis_status": "unconfirmed",
                    "basis_note": basis_note(spec),
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
            self.repository.finish_run(run_id, report["status"], report)
            return report
